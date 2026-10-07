from dataclasses import dataclass
import hashlib
import json
from pathlib import Path

import numpy as np
import torch

from backend.models.neural_network_architecture import (
    NeuralNetworkConfig,
    OracleNeuralNetwork,
    count_trainable_parameters,
)
from backend.models.neural_training_utils import (
    FeatureStandardizer,
)


EXPECTED_CLASSES = (
    0,
    1,
    2,
)


def _read_json(
    path
):

    with open(
        path,
        "r",
        encoding="utf-8",
    ) as file:

        return json.load(
            file
        )


def _sha256_file(
    path
):

    digest = hashlib.sha256()

    with open(
        path,
        "rb",
    ) as file:

        while True:

            chunk = file.read(
                1024 * 1024
            )

            if not chunk:

                break

            digest.update(
                chunk
            )

    return digest.hexdigest()


@dataclass(
    frozen=True
)
class NeuralEnsembleInference:

    raw_probabilities: tuple[
        float,
        float,
        float,
    ]

    probabilities: tuple[
        float,
        float,
        float,
    ]

    member_probabilities: tuple[
        tuple[
            float,
            float,
            float,
        ],
        ...
    ]

    predicted_class: int

    confidence: float

    probability_disagreement: float


class NeuralEnsembleArtifact:

    def __init__(
        self,
        directory,
        metadata,
        architecture,
        standardizer,
        models,
        temperature,
    ):

        self.directory = Path(
            directory
        )

        self.metadata = metadata

        self.architecture = architecture

        self.standardizer = standardizer

        self.models = tuple(
            models
        )

        self.temperature = float(
            temperature
        )


    @property
    def model_name(
        self
    ):

        return self.metadata[
            "model_name"
        ]


    @property
    def feature_names(
        self
    ):

        return tuple(
            self.metadata[
                "features"
            ]
        )


    @property
    def feature_count(
        self
    ):

        return int(
            self.metadata[
                "feature_count"
            ]
        )


    @property
    def member_count(
        self
    ):

        return len(
            self.models
        )


    def _apply_temperature(
        self,
        probabilities,
    ):

        probabilities = np.asarray(
            probabilities,
            dtype=np.float64,
        )

        probabilities = np.clip(
            probabilities,
            1e-12,
            1.0,
        )

        scaled = (
            np.log(
                probabilities
            )
            / self.temperature
        )

        scaled -= np.max(
            scaled
        )

        exponentials = np.exp(
            scaled
        )

        calibrated = (
            exponentials
            / np.sum(
                exponentials
            )
        )

        return calibrated


    def infer(
        self,
        values,
        feature_names,
    ):

        received_names = tuple(
            feature_names
        )

        if (
            received_names
            != self.feature_names
        ):

            raise ValueError(
                "Neural inference feature "
                "order does not match the "
                "production artifact."
            )

        matrix = np.asarray(
            values,
            dtype=np.float32,
        )

        if matrix.ndim == 1:

            matrix = matrix.reshape(
                1,
                -1,
            )

        if (
            matrix.shape
            != (
                1,
                self.feature_count,
            )
        ):

            raise ValueError(
                "Neural inference requires "
                "exactly one feature vector "
                f"with {self.feature_count} "
                "features."
            )

        normalized = (
            self.standardizer
            .transform(
                matrix,
                feature_names=
                    self.feature_names,
            )
        )

        tensor = torch.from_numpy(
            normalized
        )

        member_probabilities = []

        with torch.inference_mode():

            for model in self.models:

                logits = model(
                    tensor
                )

                if not torch.isfinite(
                    logits
                ).all():

                    raise RuntimeError(
                        "Neural production model "
                        "produced non-finite logits."
                    )

                probabilities = torch.softmax(
                    logits,
                    dim=1,
                )

                member_probabilities.append(
                    probabilities[
                        0
                    ]
                    .cpu()
                    .numpy()
                    .astype(
                        np.float64
                    )
                )

        member_matrix = np.stack(
            member_probabilities,
            axis=0,
        )

        raw_probabilities = np.mean(
            member_matrix,
            axis=0,
        )

        calibrated_probabilities = (
            self._apply_temperature(
                raw_probabilities
            )
        )

        if not np.isclose(
            np.sum(
                calibrated_probabilities
            ),
            1.0,
            atol=1e-10,
        ):

            raise RuntimeError(
                "Calibrated neural "
                "probabilities do not "
                "sum to one."
            )

        predicted_class = int(
            np.argmax(
                calibrated_probabilities
            )
        )

        confidence = float(
            np.max(
                calibrated_probabilities
            )
        )

        probability_disagreement = float(
            np.mean(
                np.std(
                    member_matrix,
                    axis=0,
                )
            )
        )

        return NeuralEnsembleInference(

            raw_probabilities=tuple(
                float(value)
                for value
                in raw_probabilities
            ),

            probabilities=tuple(
                float(value)
                for value
                in calibrated_probabilities
            ),

            member_probabilities=tuple(

                tuple(
                    float(value)
                    for value
                    in row
                )

                for row
                in member_matrix
            ),

            predicted_class=
                predicted_class,

            confidence=
                confidence,

            probability_disagreement=
                probability_disagreement,
        )


class NeuralModelRegistry:

    def __init__(
        self,
        registry_root=None,
    ):

        if registry_root is None:

            registry_root = (
                Path(__file__)
                .resolve()
                .parent
                / "registry"
            )

        self.registry_root = Path(
            registry_root
        )


    def _verify_checksums(
        self,
        directory,
    ):

        checksum_path = (
            directory
            / "checksums.json"
        )

        if not checksum_path.exists():

            raise FileNotFoundError(
                "Neural production artifact "
                "is missing checksums.json."
            )

        manifest = _read_json(
            checksum_path
        )

        if (
            manifest.get(
                "algorithm"
            )
            != "SHA256"
        ):

            raise RuntimeError(
                "Unsupported neural artifact "
                "checksum algorithm."
            )

        files = manifest.get(
            "files"
        )

        if not isinstance(
            files,
            dict,
        ) or not files:

            raise RuntimeError(
                "Neural artifact checksum "
                "manifest is empty."
            )

        for (
            filename,
            expected_hash,
        ) in files.items():

            # Registry filenames must remain
            # flat. Reject path traversal.
            if (
                Path(
                    filename
                ).name
                != filename
            ):

                raise RuntimeError(
                    "Invalid filename in neural "
                    "checksum manifest."
                )

            if (
                not isinstance(
                    expected_hash,
                    str,
                )
                or len(
                    expected_hash
                )
                != 64
            ):

                raise RuntimeError(
                    "Invalid SHA-256 value for "
                    f"{filename}."
                )

            path = (
                directory
                / filename
            )

            if not path.exists():

                raise FileNotFoundError(
                    "Neural production artifact "
                    f"is missing {filename}."
                )

            actual_hash = (
                _sha256_file(
                    path
                )
            )

            if (
                actual_hash
                != expected_hash
            ):

                raise RuntimeError(
                    "SHA-256 verification failed "
                    f"for {filename}."
                )

        return set(
            files.keys()
        )


    def load(
        self,
        model_name,
    ):

        directory = (
            self.registry_root
            / model_name
        )

        if not directory.is_dir():

            raise FileNotFoundError(
                "Neural model registry "
                f"does not exist: "
                f"{directory}"
            )

        checksummed_files = (
            self._verify_checksums(
                directory
            )
        )

        metadata_path = (
            directory
            / "metadata.json"
        )

        metadata = _read_json(
            metadata_path
        )

        if (
            metadata.get(
                "model_name"
            )
            != model_name
        ):

            raise RuntimeError(
                "Neural model name "
                "does not match registry "
                "directory."
            )

        if (
            metadata.get(
                "artifact_status"
            )
            != "PRODUCTION"
        ):

            raise RuntimeError(
                "Neural artifact is not "
                "marked PRODUCTION."
            )

        if (
            metadata.get(
                "production_ready"
            )
            is not True
        ):

            raise RuntimeError(
                "Neural artifact is not "
                "production ready."
            )

        features = tuple(
            metadata.get(
                "features",
                [],
            )
        )

        feature_count = int(
            metadata.get(
                "feature_count",
                0,
            )
        )

        if (
            feature_count <= 0
            or len(
                features
            )
            != feature_count
        ):

            raise RuntimeError(
                "Invalid neural feature "
                "contract."
            )

        if (
            len(
                set(
                    features
                )
            )
            != feature_count
        ):

            raise RuntimeError(
                "Neural feature names "
                "must be unique."
            )

        classes = tuple(
            int(value)
            for value
            in metadata.get(
                "classes",
                [],
            )
        )

        if (
            classes
            != EXPECTED_CLASSES
        ):

            raise RuntimeError(
                "Unexpected neural class "
                "contract."
            )

        member_files = tuple(
            metadata.get(
                "member_files",
                [],
            )
        )

        member_seeds = tuple(
            int(value)
            for value
            in metadata.get(
                "member_seeds",
                [],
            )
        )

        member_count = int(
            metadata.get(
                "member_count",
                0,
            )
        )

        if not (
            member_count
            == len(
                member_files
            )
            == len(
                member_seeds
            )
            > 0
        ):

            raise RuntimeError(
                "Invalid neural ensemble "
                "member contract."
            )

        architecture_file = (
            metadata.get(
                "architecture_file"
            )
        )

        normalization_file = (
            metadata.get(
                "normalization_file"
            )
        )

        calibration_file = (
            metadata.get(
                "calibration_file"
            )
        )

        required_files = {

            "metadata.json",
            "evaluation.json",

            architecture_file,
            normalization_file,
            calibration_file,

            *member_files,
        }

        if (
            None
            in required_files
        ):

            raise RuntimeError(
                "Neural production metadata "
                "contains missing filenames."
            )

        missing_checksums = (
            required_files
            - checksummed_files
        )

        if missing_checksums:

            raise RuntimeError(
                "Neural production files are "
                "not checksum protected: "
                f"{sorted(missing_checksums)}"
            )

        architecture_payload = (
            _read_json(
                directory
                / architecture_file
            )
        )

        architecture = (
            NeuralNetworkConfig(
                **architecture_payload
            )
        )

        if (
            architecture.input_dim
            != feature_count
        ):

            raise RuntimeError(
                "Neural architecture input "
                "dimension does not match "
                "feature contract."
            )

        if (
            architecture.num_classes
            != 3
        ):

            raise RuntimeError(
                "Neural architecture must "
                "have exactly three classes."
            )

        standardizer = (
            FeatureStandardizer.load(

                directory
                / normalization_file
            )
        )

        if (
            standardizer.feature_names
            != features
        ):

            raise RuntimeError(
                "Neural normalization "
                "feature order mismatch."
            )

        calibration = _read_json(

            directory
            / calibration_file
        )

        calibration_seeds = tuple(
            int(value)
            for value
            in calibration.get(
                "member_seeds",
                [],
            )
        )

        if (
            calibration_seeds
            != member_seeds
        ):

            raise RuntimeError(
                "Neural calibration "
                "ensemble membership "
                "does not match metadata."
            )

        temperature = float(
            calibration.get(
                "temperature"
            )
        )

        metadata_temperature = float(
            metadata.get(
                "temperature"
            )
        )

        if not np.isclose(
            temperature,
            metadata_temperature,
            atol=1e-12,
            rtol=0.0,
        ):

            raise RuntimeError(
                "Neural calibration "
                "temperature mismatch."
            )

        if not (
            0.05
            <= temperature
            <= 20.0
        ):

            raise RuntimeError(
                "Neural temperature is "
                "outside allowed bounds."
            )

        models = []

        expected_parameters = int(
            metadata.get(
                "parameters_per_member",
                0,
            )
        )

        for member_file in (
            member_files
        ):

            model = (
                OracleNeuralNetwork(
                    architecture
                )
            )

            state_dict = torch.load(

                directory
                / member_file,

                map_location=
                    "cpu",

                weights_only=
                    True,
            )

            model.load_state_dict(
                state_dict,
                strict=True,
            )

            actual_parameters = (
                count_trainable_parameters(
                    model
                )
            )

            if (
                actual_parameters
                != expected_parameters
            ):

                raise RuntimeError(
                    "Neural member parameter "
                    "count does not match "
                    "production metadata."
                )

            model.eval()

            models.append(
                model
            )

        expected_total = int(
            metadata.get(
                "total_parameters",
                0,
            )
        )

        if (
            expected_parameters
            * member_count
            != expected_total
        ):

            raise RuntimeError(
                "Neural total parameter "
                "count does not match "
                "production metadata."
            )

        artifact = (
            NeuralEnsembleArtifact(

                directory=
                    directory,

                metadata=
                    metadata,

                architecture=
                    architecture,

                standardizer=
                    standardizer,

                models=
                    models,

                temperature=
                    temperature,
            )
        )

        # Final loader-level smoke check.
        #
        # This works directly in normalized
        # feature space and proves that all
        # loaded state dictionaries produce
        # finite 3-class outputs.

        smoke_tensor = torch.zeros(
            (
                1,
                feature_count,
            ),
            dtype=torch.float32,
        )

        with torch.inference_mode():

            for model in models:

                logits = model(
                    smoke_tensor
                )

                if (
                    logits.shape
                    != (
                        1,
                        3,
                    )
                ):

                    raise RuntimeError(
                        "Neural production member "
                        "returned an invalid shape."
                    )

                if not torch.isfinite(
                    logits
                ).all():

                    raise RuntimeError(
                        "Neural production member "
                        "failed finite-output "
                        "smoke test."
                    )

        return artifact
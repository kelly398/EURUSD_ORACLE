from pathlib import Path

from backend.agents.base_agent import (
    BaseAgent
)

from backend.agents.contracts import (
    AgentCategory
)

from backend.models.feature_builder import (
    MLFeatureBuilder
)

from backend.models.model_registry import (
    ModelRegistry
)


class MLTreeEnsembleAgent(
    BaseAgent
):

    agent_id = (
        "ml_tree_ensemble"
    )

    agent_name = (
        "ML XGBoost / Random Forest"
    )

    category = (
        AgentCategory.MACHINE_LEARNING
    )

    version = "1.0.0"

    MODEL_NAME = (
        "xgboost_eurusd_m5_v1"
    )

    EXPECTED_CLASSES = (
        0,
        1,
        2
    )


    def __init__(
        self,
        model_path=None
    ):

        self.feature_builder = (
            MLFeatureBuilder()
        )


        if model_path is None:

            model_path = (

                Path(
                    __file__
                )
                .resolve()
                .parents[2]

                / "models"
                / "registry"
                / self.MODEL_NAME
            )


        self.model_registry = (
            ModelRegistry(
                str(
                    model_path
                )
            )
        )


        # Load lazily during execution.
        #
        # If the artifact is missing or
        # invalid, CouncilRunner can isolate
        # the Seat 11 failure instead of
        # preventing the whole Council from
        # being registered.
        self._artifact = None


        super().__init__()


    def _load_artifact(
        self
    ):

        if (
            self._artifact
            is not None
        ):

            return (
                self._artifact
            )


        artifact = (
            self.model_registry.load()
        )


        if not isinstance(
            artifact,
            dict
        ):

            raise RuntimeError(
                "ML model registry returned "
                "an invalid artifact."
            )


        if (
            "model"
            not in artifact
        ):

            raise RuntimeError(
                "ML artifact is missing model."
            )


        if (
            "metadata"
            not in artifact
        ):

            raise RuntimeError(
                "ML artifact is missing "
                "metadata."
            )


        model = (
            artifact["model"]
        )


        metadata = (
            artifact["metadata"]
        )


        self._validate_artifact(
            model=
                model,

            metadata=
                metadata
        )


        self._artifact = (
            artifact
        )


        return (
            self._artifact
        )


    def _validate_artifact(
        self,
        model,
        metadata
    ):

        required_metadata = (

            "model_name",

            "dataset_version",

            "feature_version",

            "label_version",

            "symbol",

            "timeframe",

            "feature_count",

            "features"
        )


        missing = [

            key

            for key
            in required_metadata

            if key not in metadata
        ]


        if missing:

            raise RuntimeError(
                "ML artifact metadata is "
                "incomplete. Missing: "
                + ", ".join(
                    missing
                )
            )


        if (
            metadata[
                "model_name"
            ]
            != self.MODEL_NAME
        ):

            raise RuntimeError(
                "Unexpected ML model artifact: "
                f"{metadata['model_name']}."
            )


        if (
            metadata[
                "feature_version"
            ]
            != self.feature_builder
            .FEATURE_VERSION
        ):

            raise RuntimeError(
                "ML artifact feature version "
                "does not match the live "
                "feature builder."
            )


        model_features = tuple(
            metadata[
                "features"
            ]
        )


        live_features = tuple(
            self.feature_builder
            .FEATURE_NAMES
        )


        if (
            len(model_features)
            != len(
                live_features
            )
        ):

            raise RuntimeError(
                "ML artifact feature count "
                "does not match the live "
                "feature builder."
            )


        if (
            len(
                set(
                    model_features
                )
            )
            != len(
                model_features
            )
        ):

            raise RuntimeError(
                "ML artifact contains "
                "duplicate feature names."
            )


        if (
            set(
                model_features
            )
            != set(
                live_features
            )
        ):

            raise RuntimeError(
                "ML artifact feature schema "
                "does not match the live "
                "feature builder."
            )


        if (
            int(
                metadata[
                    "feature_count"
                ]
            )
            != len(
                model_features
            )
        ):

            raise RuntimeError(
                "ML artifact feature_count "
                "does not match metadata "
                "feature names."
            )


        if not hasattr(
            model,
            "predict_proba"
        ):

            raise RuntimeError(
                "ML artifact does not support "
                "predict_proba."
            )


        if not hasattr(
            model,
            "classes_"
        ):

            raise RuntimeError(
                "ML artifact does not expose "
                "trained classes."
            )


        model_classes = tuple(

            int(
                value
            )

            for value
            in model.classes_
        )


        if (
            model_classes
            != self.EXPECTED_CLASSES
        ):

            raise RuntimeError(
                "Unexpected ML class mapping. "
                f"Expected "
                f"{self.EXPECTED_CLASSES}, "
                f"received "
                f"{model_classes}."
            )


    def analyze(
        self,
        context
    ):

        artifact = (
            self._load_artifact()
        )


        model = (
            artifact[
                "model"
            ]
        )


        metadata = (
            artifact[
                "metadata"
            ]
        )


        if (
            context.symbol
            != metadata[
                "symbol"
            ]
        ):

            raise RuntimeError(
                "ML model symbol does not "
                "match AgentContext."
            )


        if (
            context.timeframe
            != metadata[
                "timeframe"
            ]
        ):

            raise RuntimeError(
                "ML model timeframe does not "
                "match AgentContext."
            )


        feature_vector = (
            self.feature_builder.build(
                context
            )
        )


        if (
            feature_vector
            .feature_version
            != metadata[
                "feature_version"
            ]
        ):

            raise RuntimeError(
                "Live feature vector version "
                "does not match model artifact."
            )


        # ==================================
        # MODEL FEATURE ORDER
        # ==================================
        #
        # Training used the feature order
        # stored in model metadata.
        #
        # The live ModelFeatureVector has
        # its own canonical builder order.
        #
        # We therefore map by NAME rather
        # than assuming the two tuple orders
        # are identical.
        # ==================================

        feature_dict = (
            feature_vector.as_dict()
        )


        model_feature_names = tuple(
            metadata[
                "features"
            ]
        )


        model_values = tuple(

            float(
                feature_dict[
                    name
                ]
            )

            for name
            in model_feature_names
        )


        probabilities = (
            model.predict_proba(
                [
                    model_values
                ]
            )[0]
        )


        probability_by_class = {

            int(
                class_id
            ):
                float(
                    probability
                )

            for class_id, probability
            in zip(
                model.classes_,
                probabilities
            )
        }


        short_probability = (
            probability_by_class[
                0
            ]
        )


        neutral_probability = (
            probability_by_class[
                1
            ]
        )


        long_probability = (
            probability_by_class[
                2
            ]
        )


        # Class encoding:
        #
        # 0 = short
        # 1 = neutral
        # 2 = long
        #
        # Expected directional value:
        #
        # P(long) - P(short)
        score = (

            long_probability

            - short_probability
        )


        confidence = max(

            short_probability,

            neutral_probability,

            long_probability
        )


        predicted_class_id = max(

            probability_by_class,

            key=
                probability_by_class.get
        )


        predicted_class = {

            0:
                "short",

            1:
                "neutral",

            2:
                "long"

        }[
            predicted_class_id
        ]


        return self.build_opinion(

            context=context,

            score=score,

            confidence=confidence,

            rationale=(
                "Validated XGBoost model "
                "evaluated the snapshot-locked "
                "EUR/USD M5 feature vector. "
                f"Highest-probability class: "
                f"{predicted_class}."
            ),

            evidence={

                "model_name":
                    metadata[
                        "model_name"
                    ],

                "dataset_version":
                    metadata[
                        "dataset_version"
                    ],

                "feature_version":
                    metadata[
                        "feature_version"
                    ],

                "label_version":
                    metadata[
                        "label_version"
                    ],

                "feature_count":
                    len(
                        model_values
                    ),

                "predicted_class":
                    predicted_class,

                "short_probability":
                    short_probability,

                "neutral_probability":
                    neutral_probability,

                "long_probability":
                    long_probability,

                "directional_edge":
                    score
            }
        )
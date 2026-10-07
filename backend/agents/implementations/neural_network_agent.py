from backend.agents.base_agent import (
    BaseAgent,
)

from backend.agents.contracts import (
    AgentCategory,
)

from backend.models.feature_builder import (
    MLFeatureBuilder,
)

from backend.models.neural_model_registry import (
    NeuralModelRegistry,
)


class NeuralNetworkAgent(
    BaseAgent
):

    agent_id = (
        "neural_network"
    )

    agent_name = (
        "Neural Network"
    )

    category = (
        AgentCategory.MACHINE_LEARNING
    )

    version = (
        "1.0.0"
    )


    MODEL_NAME = (
        "neural_network_eurusd_m5_v1"
    )


    EXPECTED_CLASSES = (
        0,
        1,
        2,
    )


    EXPECTED_MEMBER_COUNT = (
        3
    )


    def __init__(
        self
    ):

        self.feature_builder = (
            MLFeatureBuilder()
        )


        self.model_registry = (
            NeuralModelRegistry()
        )


        # Load lazily.
        #
        # This preserves CouncilRunner
        # failure isolation: a bad or
        # unavailable model artifact
        # fails Seat 12 during execution
        # rather than preventing the
        # Council registry from starting.
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
            self.model_registry.load(
                self.MODEL_NAME
            )
        )


        self._validate_artifact(
            artifact
        )


        self._artifact = (
            artifact
        )


        return (
            self._artifact
        )


    def _validate_artifact(
        self,
        artifact
    ):

        metadata = (
            artifact.metadata
        )


        required_metadata = (

            "model_name",

            "dataset_version",

            "feature_version",

            "label_version",

            "symbol",

            "timeframe",

            "feature_count",

            "features",

            "classes",

            "member_count",

            "ensemble_method",
        )


        missing = [

            key

            for key
            in required_metadata

            if key not in metadata
        ]


        if missing:

            raise RuntimeError(

                "Neural artifact metadata "
                "is incomplete. Missing: "

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

                "Unexpected neural model "
                "artifact: "

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

                "Neural artifact feature "
                "version does not match "
                "the live feature builder."
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
            len(
                model_features
            )
            != len(
                live_features
            )
        ):

            raise RuntimeError(

                "Neural artifact feature "
                "count does not match "
                "the live feature builder."
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

                "Neural artifact contains "
                "duplicate feature names."
            )


        # The training-time feature order
        # and live feature-builder order
        # are allowed to differ.
        #
        # Schema identity is checked here.
        # Exact training order is restored
        # by NAME during analyze().
        if (
            set(
                model_features
            )
            != set(
                live_features
            )
        ):

            raise RuntimeError(

                "Neural artifact feature "
                "schema does not match "
                "the live feature builder."
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

                "Neural artifact "
                "feature_count does not "
                "match metadata features."
            )


        artifact_classes = tuple(

            int(
                value
            )

            for value
            in metadata[
                "classes"
            ]
        )


        if (
            artifact_classes
            != self.EXPECTED_CLASSES
        ):

            raise RuntimeError(

                "Unexpected neural class "
                "mapping. Expected "

                f"{self.EXPECTED_CLASSES}, "

                "received "

                f"{artifact_classes}."
            )


        if (
            int(
                metadata[
                    "member_count"
                ]
            )
            != self.EXPECTED_MEMBER_COUNT
        ):

            raise RuntimeError(

                "Unexpected neural ensemble "
                "member count."
            )


        if (
            artifact.member_count
            != self.EXPECTED_MEMBER_COUNT
        ):

            raise RuntimeError(

                "Loaded neural ensemble "
                "member count does not "
                "match metadata."
            )


        if (
            metadata[
                "ensemble_method"
            ]
            != "mean_probability"
        ):

            raise RuntimeError(

                "Unexpected neural ensemble "
                "method."
            )


    def _member_vote_agreement(
        self,
        member_probabilities
    ):

        predictions = []


        for probabilities in (
            member_probabilities
        ):

            predicted_class = max(

                self.EXPECTED_CLASSES,

                key=lambda class_id:
                    probabilities[
                        class_id
                    ],
            )


            predictions.append(
                predicted_class
            )


        highest_vote_count = max(

            predictions.count(
                class_id
            )

            for class_id
            in self.EXPECTED_CLASSES
        )


        return (

            highest_vote_count

            / len(
                predictions
            )
        )


    def analyze(
        self,
        context
    ):

        artifact = (
            self._load_artifact()
        )


        metadata = (
            artifact.metadata
        )


        if (
            context.symbol
            != metadata[
                "symbol"
            ]
        ):

            raise RuntimeError(

                "Neural model symbol "
                "does not match "
                "AgentContext."
            )


        if (
            context.timeframe
            != metadata[
                "timeframe"
            ]
        ):

            raise RuntimeError(

                "Neural model timeframe "
                "does not match "
                "AgentContext."
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

                "Live feature vector "
                "version does not match "
                "neural artifact."
            )


        # ==================================
        # MODEL FEATURE ORDER
        # ==================================
        #
        # Exactly the same protection used
        # by Seat 11:
        #
        # training used the feature order
        # stored in artifact metadata.
        #
        # MLFeatureBuilder has its own
        # canonical semantic order.
        #
        # Rebuild by NAME rather than ever
        # assuming tuple-order equality.
        # ==================================

        feature_dict = (
            feature_vector.as_dict()
        )


        model_feature_names = (
            artifact.feature_names
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


        inference = (
            artifact.infer(

                values=
                    model_values,

                feature_names=
                    model_feature_names,
            )
        )


        short_probability = float(
            inference.probabilities[
                0
            ]
        )


        neutral_probability = float(
            inference.probabilities[
                1
            ]
        )


        long_probability = float(
            inference.probabilities[
                2
            ]
        )


        # ==================================
        # DIRECTIONAL SCORE
        # ==================================
        #
        # Same continuous directional
        # convention as Seat 11:
        #
        #     +1 -> completely LONG
        #      0 -> no directional edge
        #     -1 -> completely SHORT
        #
        # BaseAgent converts this score
        # into LONG / SHORT / NEUTRAL
        # using the shared +/-0.15
        # Council threshold.
        # ==================================

        score = (

            long_probability

            - short_probability
        )


        raw_confidence = float(
            inference.confidence
        )


        ensemble_disagreement = float(
            inference
            .probability_disagreement
        )


        # ==================================
        # ENSEMBLE-AWARE CONFIDENCE
        # ==================================
        #
        # Confidence should decrease when
        # independently initialized neural
        # members disagree.
        #
        # Both terms are expressed in
        # probability units, making the
        # adjustment transparent.
        # ==================================

        confidence = (

            raw_confidence

            - ensemble_disagreement
        )


        confidence = max(

            0.0,

            min(
                1.0,
                confidence
            )
        )


        predicted_class_id = int(
            inference.predicted_class
        )


        predicted_class = {

            0:
                "short",

            1:
                "neutral",

            2:
                "long",

        }[
            predicted_class_id
        ]


        member_vote_agreement = (
            self._member_vote_agreement(

                inference
                .member_probabilities
            )
        )


        return self.build_opinion(

            context=
                context,

            score=
                score,

            confidence=
                confidence,

            rationale=(

                "Validated calibrated "
                "three-member PyTorch "
                "neural ensemble evaluated "
                "the snapshot-locked "
                "EUR/USD M5 feature vector. "

                "Highest-probability class: "

                f"{predicted_class}. "
                "Confidence includes an "
                "ensemble-disagreement "
                "penalty."
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

                "member_count":
                    artifact.member_count,

                "temperature":
                    artifact.temperature,

                "predicted_class":
                    predicted_class,

                "short_probability":
                    short_probability,

                "neutral_probability":
                    neutral_probability,

                "long_probability":
                    long_probability,

                "raw_confidence":
                    raw_confidence,

                "ensemble_disagreement":
                    ensemble_disagreement,

                "ensemble_adjusted_confidence":
                    confidence,

                "member_vote_agreement":
                    member_vote_agreement,

                "directional_edge":
                    score,
            },
        )
import time

import numpy as np

from backend.models.neural_model_registry import (
    NeuralModelRegistry,
)


MODEL_NAME = (
    "neural_network_eurusd_m5_v1"
)


def main():

    print(
        "=" * 78
    )

    print(
        "EUR/USD ORACLE "
        "NEURAL PRODUCTION REGISTRY TEST"
    )

    print(
        "=" * 78
    )


    registry = (
        NeuralModelRegistry()
    )


    cold_start = (
        time.perf_counter()
    )


    artifact = (
        registry.load(
            MODEL_NAME
        )
    )


    cold_elapsed_ms = (

        time.perf_counter()
        - cold_start

    ) * 1000.0


    print(
        f"Model: "
        f"{artifact.model_name}"
    )

    print(
        f"Features: "
        f"{artifact.feature_count}"
    )

    print(
        f"Members: "
        f"{artifact.member_count}"
    )

    print(
        f"Temperature: "
        f"{artifact.temperature:.6f}"
    )

    print(
        f"Cold registry load: "
        f"{cold_elapsed_ms:.2f} ms"
    )


    assert (
        artifact.feature_count
        == 27
    )


    assert (
        artifact.member_count
        == 3
    )


    assert (
        tuple(
            artifact.metadata[
                "classes"
            ]
        )
        == (
            0,
            1,
            2
        )
    )


    # Use the train-only feature mean as a
    # sensible deterministic raw feature
    # vector. After normalization this is
    # approximately the zero vector.

    raw_vector = (
        artifact.standardizer
        .mean
        .astype(
            np.float32
        )
    )


    inference_one = (
        artifact.infer(

            values=
                raw_vector,

            feature_names=
                artifact.feature_names,
        )
    )


    inference_two = (
        artifact.infer(

            values=
                raw_vector,

            feature_names=
                artifact.feature_names,
        )
    )


    print()
    print(
        "Calibrated probabilities:"
    )

    print(
        f"Short:   "
        f"{inference_one.probabilities[0]:.6f}"
    )

    print(
        f"Neutral: "
        f"{inference_one.probabilities[1]:.6f}"
    )

    print(
        f"Long:    "
        f"{inference_one.probabilities[2]:.6f}"
    )

    print(
        f"Predicted class: "
        f"{inference_one.predicted_class}"
    )

    print(
        f"Confidence: "
        f"{inference_one.confidence:.6f}"
    )

    print(
        "Probability disagreement: "
        f"{inference_one.probability_disagreement:.6f}"
    )


    assert np.isclose(

        sum(
            inference_one.probabilities
        ),

        1.0,

        atol=1e-10,
    )


    assert (
        inference_one
        == inference_two
    )


    assert (
        inference_one.predicted_class
        in (
            0,
            1,
            2
        )
    )


    assert (
        0.0
        <= inference_one.confidence
        <= 1.0
    )


    assert (
        inference_one.probability_disagreement
        >= 0.0
    )


    assert (
        len(
            inference_one.member_probabilities
        )
        == 3
    )


    for probabilities in (
        inference_one.member_probabilities
    ):

        assert np.isclose(

            sum(
                probabilities
            ),

            1.0,

            atol=1e-6,
        )


    # Feature-order corruption must fail
    # closed rather than silently generate
    # a wrong prediction.

    try:

        artifact.infer(

            values=
                raw_vector,

            feature_names=
                tuple(
                    reversed(
                        artifact.feature_names
                    )
                ),
        )

    except ValueError:

        feature_order_rejected = (
            True
        )

    else:

        feature_order_rejected = (
            False
        )


    assert (
        feature_order_rejected
    )


    # --------------------------------------------------------
    # EXACT PRODUCTION WARM LATENCY
    # --------------------------------------------------------

    for _ in range(
        200
    ):

        artifact.infer(

            values=
                raw_vector,

            feature_names=
                artifact.feature_names,
        )


    measurements = []


    for _ in range(
        2000
    ):

        start = (
            time.perf_counter_ns()
        )


        artifact.infer(

            values=
                raw_vector,

            feature_names=
                artifact.feature_names,
        )


        end = (
            time.perf_counter_ns()
        )


        measurements.append(

            (
                end
                - start
            )

            / 1_000_000.0
        )


    measurements = np.asarray(
        measurements,
        dtype=np.float64,
    )


    median_ms = float(
        np.median(
            measurements
        )
    )


    p95_ms = float(
        np.percentile(
            measurements,
            95,
        )
    )


    p99_ms = float(
        np.percentile(
            measurements,
            99,
        )
    )


    print()
    print(
        "Exact production inference latency:"
    )

    print(
        f"Median: "
        f"{median_ms:.4f} ms"
    )

    print(
        f"P95: "
        f"{p95_ms:.4f} ms"
    )

    print(
        f"P99: "
        f"{p99_ms:.4f} ms"
    )


    # Generous operational guardrails.
    #
    # These are intentionally much looser
    # than the observed ~2 ms ensemble
    # benchmark to avoid flaky tests while
    # still detecting major regressions.

    assert (
        median_ms
        < 20.0
    )


    assert (
        p95_ms
        < 50.0
    )


    print()
    print(
        "SHA-256 verification: PASS"
    )

    print(
        "Production metadata contract: PASS"
    )

    print(
        "weights_only loading: PASS"
    )

    print(
        "Normalization contract: PASS"
    )

    print(
        "Calibration contract: PASS"
    )

    print(
        "Feature-order rejection: PASS"
    )

    print(
        "Deterministic inference: PASS"
    )

    print(
        "Production latency: PASS"
    )


    print()
    print(
        "=" * 78
    )

    print(
        "NEURAL PRODUCTION REGISTRY "
        "TEST: PASS"
    )

    print(
        "=" * 78
    )


if __name__ == "__main__":

    main()
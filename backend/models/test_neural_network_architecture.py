import torch

from backend.models.neural_network_architecture import (
    NeuralNetworkConfig,
    OracleNeuralNetwork,
    count_trainable_parameters
)


def main():

    print(
        "=" * 70
    )

    print(
        "EUR/USD ORACLE "
        "NEURAL NETWORK ARCHITECTURE TEST"
    )

    print(
        "=" * 70
    )


    torch.manual_seed(
        42
    )


    config = (
        NeuralNetworkConfig()
    )


    model = (
        OracleNeuralNetwork(
            config
        )
    )


    parameter_count = (
        count_trainable_parameters(
            model
        )
    )


    print(
        f"Input features: "
        f"{config.input_dim}"
    )


    print(
        f"Hidden dimension: "
        f"{config.hidden_dim}"
    )


    print(
        f"Residual blocks: "
        f"{config.residual_blocks}"
    )


    print(
        f"Trainable parameters: "
        f"{parameter_count:,}"
    )


    assert (
        200_000
        <= parameter_count
        <= 400_000
    )


    x = (
        torch.randn(
            32,
            27,
            dtype=torch.float32
        )
    )


    model.eval()


    with torch.inference_mode():

        logits_one = (
            model(
                x
            )
        )


        logits_two = (
            model(
                x
            )
        )


        gates = (
            model.feature_gates(
                x
            )
        )


    print(
        f"Logit shape: "
        f"{tuple(logits_one.shape)}"
    )


    print(
        f"Gate shape: "
        f"{tuple(gates.shape)}"
    )


    print(
        "Gate mean: "
        f"{gates.mean().item():.6f}"
    )


    assert (
        logits_one.shape
        == (
            32,
            3
        )
    )


    assert (
        gates.shape
        == (
            32,
            27
        )
    )


    assert (
        torch.isfinite(
            logits_one
        ).all()
    )


    assert (
        torch.isfinite(
            gates
        ).all()
    )


    assert (
        torch.all(
            gates >= 0.0
        )
    )


    assert (
        torch.all(
            gates <= 1.0
        )
    )


    # Evaluation inference must be
    # deterministic.
    assert (
        torch.equal(
            logits_one,
            logits_two
        )
    )


    probabilities = (
        torch.softmax(
            logits_one,
            dim=1
        )
    )


    probability_sums = (
        probabilities.sum(
            dim=1
        )
    )


    assert torch.allclose(

        probability_sums,

        torch.ones_like(
            probability_sums
        ),

        atol=1e-6
    )


    print(
        "\nForward contract: PASS"
    )

    print(
        "Feature gate contract: PASS"
    )

    print(
        "Probability contract: PASS"
    )

    print(
        "Deterministic eval inference: PASS"
    )


    print(
        "\n"
        + "=" * 70
    )

    print(
        "NEURAL NETWORK "
        "ARCHITECTURE TEST: PASS"
    )

    print(
        "=" * 70
    )


if __name__ == "__main__":

    main()
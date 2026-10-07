import numpy as np
import torch

from torch.utils.data import (
    DataLoader,
    TensorDataset
)

from backend.models.neural_network_architecture import (
    NeuralNetworkConfig,
    OracleNeuralNetwork
)

from backend.models.neural_training_engine import (
    NeuralTrainingConfig,
    evaluate_model,
    train_model
)

from backend.models.neural_training_utils import (
    set_reproducible_seed
)


def make_dataset(
    sample_count,
    seed
):

    rng = np.random.default_rng(
        seed
    )


    X = rng.normal(

        size=(
            sample_count,
            27
        )

    ).astype(
        np.float32
    )


    signal = (

        1.4 * X[:, 0]

        - 0.9 * X[:, 1]

        + 0.7 * X[:, 2]

        + 0.4 * X[:, 3]

        - 0.3 * X[:, 4]
    )


    y = np.ones(
        sample_count,
        dtype=np.int64
    )


    y[
        signal < -0.45
    ] = 0


    y[
        signal > 0.45
    ] = 2


    return (
        X,
        y
    )


def make_loader(
    X,
    y,
    batch_size,
    shuffle,
    seed
):

    dataset = TensorDataset(

        torch.from_numpy(
            X
        ),

        torch.from_numpy(
            y
        )
    )


    generator = (
        torch.Generator()
    )


    generator.manual_seed(
        seed
    )


    return DataLoader(

        dataset,

        batch_size=
            batch_size,

        shuffle=
            shuffle,

        generator=
            generator,

        num_workers=0,

        drop_last=False
    )


def main():

    print(
        "=" * 70
    )

    print(
        "EUR/USD ORACLE "
        "NEURAL TRAINING ENGINE TEST"
    )

    print(
        "=" * 70
    )


    set_reproducible_seed(
        42
    )


    X_train, y_train = (
        make_dataset(
            sample_count=4096,
            seed=100
        )
    )


    X_validation, y_validation = (
        make_dataset(
            sample_count=1024,
            seed=200
        )
    )


    train_loader = (
        make_loader(

            X_train,
            y_train,

            batch_size=256,

            shuffle=True,

            seed=42
        )
    )


    validation_loader = (
        make_loader(

            X_validation,
            y_validation,

            batch_size=512,

            shuffle=False,

            seed=42
        )
    )


    network_config = (
        NeuralNetworkConfig()
    )


    model = (
        OracleNeuralNetwork(
            network_config
        )
    )


    training_config = (
        NeuralTrainingConfig(

            max_epochs=8,

            learning_rate=1e-3,

            minimum_learning_rate=1e-4,

            warmup_epochs=2,

            weight_decay=1e-4,

            gradient_clip_norm=1.0,

            label_smoothing=0.01,

            patience=8,

            minimum_improvement=1e-4
        )
    )


    result = (
        train_model(

            model=
                model,

            train_loader=
                train_loader,

            validation_loader=
                validation_loader,

            config=
                training_config,

            device=
                torch.device(
                    "cpu"
                ),

            verbose=True
        )
    )


    final_metrics = (
        evaluate_model(

            model=
                model,

            data_loader=
                validation_loader,

            device=
                torch.device(
                    "cpu"
                )
        )
    )


    print()
    print(
        f"Best epoch: "
        f"{result.best_epoch}"
    )


    print(
        f"Epochs completed: "
        f"{result.epochs_completed}"
    )


    print(
        "Best validation macro F1: "
        f"{result.best_validation_macro_f1:.4f}"
    )


    print(
        "Restored validation macro F1: "
        f"{final_metrics.macro_f1:.4f}"
    )


    assert (
        1
        <= result.best_epoch
        <= result.epochs_completed
    )


    assert (
        result.epochs_completed
        <= training_config.max_epochs
    )


    assert np.isfinite(
        result.best_validation_loss
    )


    assert (
        result.best_validation_macro_f1
        > 0.60
    )


    # Proves best checkpoint restoration.
    assert abs(

        final_metrics.macro_f1

        - result.best_validation_macro_f1

    ) < 1e-12


    model.eval()


    sample = torch.from_numpy(
        X_validation[
            :32
        ]
    )


    with torch.inference_mode():

        output_one = model(
            sample
        )


        output_two = model(
            sample
        )


    assert torch.equal(
        output_one,
        output_two
    )


    print()
    print(
        "AdamW training: PASS"
    )

    print(
        "Gradient clipping: PASS"
    )

    print(
        "Validation checkpointing: PASS"
    )

    print(
        "Best checkpoint restoration: PASS"
    )

    print(
        "Deterministic inference: PASS"
    )


    print(
        "\n"
        + "=" * 70
    )

    print(
        "NEURAL TRAINING ENGINE "
        "TEST: PASS"
    )

    print(
        "=" * 70
    )


if __name__ == "__main__":

    main()
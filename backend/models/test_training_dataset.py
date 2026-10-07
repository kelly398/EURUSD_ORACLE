from backend.models.training_dataset import (
    TrainingDatasetLoader,
)


def main():

    loader = TrainingDatasetLoader(
        dataset_version=(
            "eurusd_m5_full_20251002_20261001_v1"
        ),
        feature_version="eurusd_m5_v1",
        label_version=(
            "eurusd_m5_direction_v1"
        ),
    )

    dataset = loader.load()

    print("=" * 70)
    print("TRAINING DATASET LOADER TEST")
    print("=" * 70)

    print("Features:", len(dataset.feature_names))

    print(
        "Train:",
        len(dataset.X_train)
    )

    print(
        "Validation:",
        len(dataset.X_validation)
    )

    print(
        "Test:",
        len(dataset.X_test)
    )

    print("=" * 70)
    print("TRAINING DATASET LOADER: PASS")


if __name__ == "__main__":
    main()
    
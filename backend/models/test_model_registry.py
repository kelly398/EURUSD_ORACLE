from backend.models.model_registry import ModelRegistry


def main():

    registry = ModelRegistry(
        "backend/models/registry/xgboost_eurusd_m5_v1"
    )

    artifact = registry.load()

    print("=" * 70)
    print("MODEL REGISTRY TEST")
    print("=" * 70)

    print(
        "Model:",
        type(artifact["model"])
    )

    print(
        "Version:",
        artifact["metadata"]["model_name"]
    )

    print(
        "Dataset:",
        artifact["metadata"]["dataset_version"]
    )

    print("=" * 70)
    print("MODEL REGISTRY: PASS")


if __name__ == "__main__":
    main()
    
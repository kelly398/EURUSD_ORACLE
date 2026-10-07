import argparse

import backend.models.train_neural_network_experiment as experiment


ALLOWED_SEEDS = (
    11,
    29,
    47
)


def main():

    parser = argparse.ArgumentParser(

        description=(
            "Run a fixed ORACLE neural "
            "network experiment using "
            "a specified reproducibility seed."
        )
    )


    parser.add_argument(

        "--seed",

        type=int,

        required=True,

        choices=ALLOWED_SEEDS
    )


    arguments = parser.parse_args()


    seed = arguments.seed


    # The experiment module intentionally
    # owns the architecture and training
    # configuration.
    #
    # We change ONLY the random seed and
    # output directory here. This prevents
    # accidental hyperparameter drift
    # between ensemble members.

    experiment.SEED = (
        seed
    )


    experiment.EXPERIMENT_DIR = (

        experiment.REPO_ROOT

        / "backend"
        / "models"
        / "experiments"

        / (
            f"{experiment.MODEL_NAME}"
            f"_seed_{seed:02d}"
        )
    )


    experiment.main()


if __name__ == "__main__":

    main()
import copy
import math

from dataclasses import (
    asdict,
    dataclass
)

import numpy as np
import torch

from sklearn.metrics import (
    accuracy_score,
    f1_score
)

from torch import nn


@dataclass(
    frozen=True
)
class NeuralTrainingConfig:

    max_epochs: int = 100

    learning_rate: float = 3e-4

    minimum_learning_rate: float = 1e-5

    warmup_epochs: int = 3

    weight_decay: float = 1e-4

    gradient_clip_norm: float = 1.0

    label_smoothing: float = 0.02

    patience: int = 15

    minimum_improvement: float = 1e-4


    def validate(
        self
    ):

        if self.max_epochs <= 0:

            raise ValueError(
                "max_epochs must be positive."
            )


        if self.learning_rate <= 0.0:

            raise ValueError(
                "learning_rate must be positive."
            )


        if self.minimum_learning_rate <= 0.0:

            raise ValueError(
                "minimum_learning_rate "
                "must be positive."
            )


        if (
            self.minimum_learning_rate
            > self.learning_rate
        ):

            raise ValueError(
                "minimum_learning_rate cannot "
                "exceed learning_rate."
            )


        if self.warmup_epochs < 0:

            raise ValueError(
                "warmup_epochs cannot be negative."
            )


        if (
            self.warmup_epochs
            >= self.max_epochs
        ):

            raise ValueError(
                "warmup_epochs must be smaller "
                "than max_epochs."
            )


        if self.weight_decay < 0.0:

            raise ValueError(
                "weight_decay cannot be negative."
            )


        if self.gradient_clip_norm <= 0.0:

            raise ValueError(
                "gradient_clip_norm "
                "must be positive."
            )


        if not (
            0.0
            <= self.label_smoothing
            < 1.0
        ):

            raise ValueError(
                "label_smoothing must be "
                "between 0 and 1."
            )


        if self.patience <= 0:

            raise ValueError(
                "patience must be positive."
            )


    def as_dict(
        self
    ):

        return asdict(
            self
        )


@dataclass(
    frozen=True
)
class EvaluationMetrics:

    loss: float

    accuracy: float

    macro_f1: float

    sample_count: int


@dataclass(
    frozen=True
)
class EpochRecord:

    epoch: int

    learning_rate: float

    training_loss: float

    validation_loss: float

    validation_accuracy: float

    validation_macro_f1: float


@dataclass
class NeuralTrainingResult:

    best_epoch: int

    best_validation_loss: float

    best_validation_accuracy: float

    best_validation_macro_f1: float

    epochs_completed: int

    stopped_early: bool

    history: list[
        EpochRecord
    ]


    def history_as_dicts(
        self
    ):

        return [
            asdict(
                record
            )
            for record
            in self.history
        ]


def learning_rate_for_epoch(
    config,
    epoch
):

    if (
        epoch < 1
        or epoch > config.max_epochs
    ):

        raise ValueError(
            "epoch is outside configured "
            "training range."
        )


    if (
        config.warmup_epochs > 0
        and epoch <= config.warmup_epochs
    ):

        warmup_fraction = (
            epoch
            / config.warmup_epochs
        )


        return (
            config.learning_rate
            * warmup_fraction
        )


    decay_epochs = (
        config.max_epochs
        - config.warmup_epochs
    )


    elapsed_decay_epochs = (
        epoch
        - config.warmup_epochs
    )


    progress = (

        elapsed_decay_epochs

        / decay_epochs
    )


    progress = min(
        1.0,
        max(
            0.0,
            progress
        )
    )


    cosine = (
        0.5
        * (
            1.0
            + math.cos(
                math.pi
                * progress
            )
        )
    )


    return (

        config.minimum_learning_rate

        + (
            config.learning_rate
            - config.minimum_learning_rate
        )
        * cosine
    )


def set_optimizer_learning_rate(
    optimizer,
    learning_rate
):

    for parameter_group in (
        optimizer.param_groups
    ):

        parameter_group[
            "lr"
        ] = learning_rate


def evaluate_model(
    model,
    data_loader,
    device
):

    model.eval()


    criterion = (
        nn.CrossEntropyLoss()
    )


    total_loss = 0.0

    total_samples = 0

    predictions = []

    targets = []


    with torch.inference_mode():

        for X_batch, y_batch in (
            data_loader
        ):

            X_batch = X_batch.to(
                device
            )


            y_batch = y_batch.to(
                device
            )


            logits = model(
                X_batch
            )


            if not torch.isfinite(
                logits
            ).all():

                raise RuntimeError(
                    "Model produced non-finite "
                    "validation logits."
                )


            loss = criterion(
                logits,
                y_batch
            )


            batch_size = (
                len(
                    y_batch
                )
            )


            total_loss += (
                float(
                    loss.item()
                )
                * batch_size
            )


            total_samples += (
                batch_size
            )


            predicted_classes = (
                torch.argmax(
                    logits,
                    dim=1
                )
            )


            predictions.append(

                predicted_classes
                .cpu()
                .numpy()
            )


            targets.append(

                y_batch
                .cpu()
                .numpy()
            )


    if total_samples == 0:

        raise RuntimeError(
            "Evaluation loader contained "
            "zero samples."
        )


    predictions = np.concatenate(
        predictions
    )


    targets = np.concatenate(
        targets
    )


    return EvaluationMetrics(

        loss=(
            total_loss
            / total_samples
        ),

        accuracy=float(
            accuracy_score(
                targets,
                predictions
            )
        ),

        macro_f1=float(
            f1_score(
                targets,
                predictions,
                average="macro",
                zero_division=0
            )
        ),

        sample_count=int(
            total_samples
        )
    )


def _validation_is_better(
    candidate,
    best,
    minimum_improvement
):

    if best is None:

        return True


    f1_improvement = (

        candidate.macro_f1

        - best.macro_f1
    )


    if (
        f1_improvement
        > minimum_improvement
    ):

        return True


    # If F1 is effectively tied,
    # prefer the better calibrated /
    # lower-loss checkpoint.
    if (
        abs(
            f1_improvement
        )
        <= minimum_improvement
        and candidate.loss < best.loss
    ):

        return True


    return False


def train_model(
    model,
    train_loader,
    validation_loader,
    config,
    device=None,
    verbose=True
):

    config.validate()


    if device is None:

        device = torch.device(
            "cpu"
        )


    model = model.to(
        device
    )


    optimizer = torch.optim.AdamW(

        model.parameters(),

        lr=config.learning_rate,

        weight_decay=
            config.weight_decay
    )


    training_criterion = (
        nn.CrossEntropyLoss(
            label_smoothing=
                config.label_smoothing
        )
    )


    best_state_dict = None

    best_metrics = None

    best_epoch = 0

    epochs_without_improvement = 0

    history = []

    stopped_early = False


    for epoch in range(
        1,
        config.max_epochs + 1
    ):

        learning_rate = (
            learning_rate_for_epoch(
                config,
                epoch
            )
        )


        set_optimizer_learning_rate(
            optimizer,
            learning_rate
        )


        model.train()


        total_training_loss = 0.0

        total_training_samples = 0


        for X_batch, y_batch in (
            train_loader
        ):

            X_batch = X_batch.to(
                device
            )


            y_batch = y_batch.to(
                device
            )


            optimizer.zero_grad(
                set_to_none=True
            )


            logits = model(
                X_batch
            )


            if not torch.isfinite(
                logits
            ).all():

                raise RuntimeError(
                    "Model produced non-finite "
                    "training logits."
                )


            loss = training_criterion(
                logits,
                y_batch
            )


            if not torch.isfinite(
                loss
            ):

                raise RuntimeError(
                    "Training loss became "
                    "non-finite."
                )


            loss.backward()


            torch.nn.utils.clip_grad_norm_(

                model.parameters(),

                max_norm=
                    config.gradient_clip_norm
            )


            optimizer.step()


            batch_size = (
                len(
                    y_batch
                )
            )


            total_training_loss += (
                float(
                    loss.item()
                )
                * batch_size
            )


            total_training_samples += (
                batch_size
            )


        if (
            total_training_samples
            == 0
        ):

            raise RuntimeError(
                "Training loader contained "
                "zero samples."
            )


        training_loss = (

            total_training_loss

            / total_training_samples
        )


        validation_metrics = (
            evaluate_model(

                model=
                    model,

                data_loader=
                    validation_loader,

                device=
                    device
            )
        )


        record = EpochRecord(

            epoch=
                epoch,

            learning_rate=float(
                learning_rate
            ),

            training_loss=float(
                training_loss
            ),

            validation_loss=float(
                validation_metrics.loss
            ),

            validation_accuracy=float(
                validation_metrics.accuracy
            ),

            validation_macro_f1=float(
                validation_metrics.macro_f1
            )
        )


        history.append(
            record
        )


        improved = (
            _validation_is_better(

                candidate=
                    validation_metrics,

                best=
                    best_metrics,

                minimum_improvement=
                    config.minimum_improvement
            )
        )


        if improved:

            best_state_dict = (
                copy.deepcopy(
                    model.state_dict()
                )
            )


            best_metrics = (
                validation_metrics
            )


            best_epoch = (
                epoch
            )


            epochs_without_improvement = (
                0
            )

        else:

            epochs_without_improvement += (
                1
            )


        if verbose:

            marker = (
                " *"
                if improved
                else ""
            )


            print(

                f"Epoch "
                f"{epoch:03d} | "

                f"lr="
                f"{learning_rate:.7f} | "

                f"train_loss="
                f"{training_loss:.6f} | "

                f"val_loss="
                f"{validation_metrics.loss:.6f} | "

                f"val_acc="
                f"{validation_metrics.accuracy:.4f} | "

                f"val_f1="
                f"{validation_metrics.macro_f1:.4f}"

                f"{marker}"
            )


        if (
            epochs_without_improvement
            >= config.patience
        ):

            stopped_early = (
                True
            )


            if verbose:

                print(

                    "Early stopping triggered "
                    f"after epoch {epoch}."
                )


            break


    if (
        best_state_dict
        is None
        or best_metrics
        is None
    ):

        raise RuntimeError(
            "Training completed without "
            "a valid checkpoint."
        )


    # Critical:
    # restore the best chronological
    # validation checkpoint, not the
    # final epoch.
    model.load_state_dict(
        best_state_dict
    )


    model.eval()


    return NeuralTrainingResult(

        best_epoch=
            best_epoch,

        best_validation_loss=float(
            best_metrics.loss
        ),

        best_validation_accuracy=float(
            best_metrics.accuracy
        ),

        best_validation_macro_f1=float(
            best_metrics.macro_f1
        ),

        epochs_completed=
            len(
                history
            ),

        stopped_early=
            stopped_early,

        history=
            history
    )
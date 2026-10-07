from dataclasses import (
    asdict,
    dataclass
)

import torch

from torch import nn


@dataclass(
    frozen=True
)
class NeuralNetworkConfig:

    input_dim: int = 27

    hidden_dim: int = 128

    expansion_dim: int = 256

    residual_blocks: int = 4

    head_dim: int = 64

    dropout: float = 0.12

    residual_dropout: float = 0.06

    num_classes: int = 3


    def as_dict(
        self
    ):

        return asdict(
            self
        )


class FeatureGate(
    nn.Module
):

    def __init__(
        self,
        input_dim
    ):

        super().__init__()


        self.norm = (
            nn.LayerNorm(
                input_dim
            )
        )


        self.gate = (
            nn.Linear(
                input_dim,
                input_dim
            )
        )


        # Begin close to passthrough.
        #
        # sigmoid(2.0) ~= 0.88
        # prevents the new network from
        # initially suppressing features
        # too aggressively.
        nn.init.zeros_(
            self.gate.weight
        )


        nn.init.constant_(
            self.gate.bias,
            2.0
        )


    def forward(
        self,
        x
    ):

        normalized = (
            self.norm(
                x
            )
        )


        gates = (
            torch.sigmoid(
                self.gate(
                    normalized
                )
            )
        )


        return (
            x * gates
        )


    def get_gates(
        self,
        x
    ):

        normalized = (
            self.norm(
                x
            )
        )


        return torch.sigmoid(
            self.gate(
                normalized
            )
        )


class ResidualBlock(
    nn.Module
):

    def __init__(
        self,
        hidden_dim,
        expansion_dim,
        dropout,
        residual_dropout
    ):

        super().__init__()


        self.norm = (
            nn.LayerNorm(
                hidden_dim
            )
        )


        self.linear_one = (
            nn.Linear(
                hidden_dim,
                expansion_dim
            )
        )


        self.activation = (
            nn.SiLU()
        )


        self.dropout_one = (
            nn.Dropout(
                dropout
            )
        )


        self.linear_two = (
            nn.Linear(
                expansion_dim,
                hidden_dim
            )
        )


        self.dropout_two = (
            nn.Dropout(
                residual_dropout
            )
        )


        # Small learned residual scale
        # stabilizes deeper optimization.
        self.residual_scale = (
            nn.Parameter(
                torch.tensor(
                    0.10,
                    dtype=torch.float32
                )
            )
        )


    def forward(
        self,
        x
    ):

        residual = (
            x
        )


        branch = (
            self.norm(
                x
            )
        )


        branch = (
            self.linear_one(
                branch
            )
        )


        branch = (
            self.activation(
                branch
            )
        )


        branch = (
            self.dropout_one(
                branch
            )
        )


        branch = (
            self.linear_two(
                branch
            )
        )


        branch = (
            self.dropout_two(
                branch
            )
        )


        return (

            residual

            + (
                self.residual_scale
                * branch
            )
        )


class OracleNeuralNetwork(
    nn.Module
):

    def __init__(
        self,
        config=None
    ):

        super().__init__()


        if config is None:

            config = (
                NeuralNetworkConfig()
            )


        self.config = (
            config
        )


        if (
            config.input_dim
            <= 0
        ):

            raise ValueError(
                "input_dim must be "
                "greater than zero."
            )


        if (
            config.num_classes
            != 3
        ):

            raise ValueError(
                "ORACLE neural network "
                "requires exactly "
                "three classes."
            )


        self.feature_gate = (
            FeatureGate(
                config.input_dim
            )
        )


        self.input_projection = (
            nn.Linear(
                config.input_dim,
                config.hidden_dim
            )
        )


        self.input_norm = (
            nn.LayerNorm(
                config.hidden_dim
            )
        )


        self.input_activation = (
            nn.SiLU()
        )


        self.blocks = (
            nn.ModuleList(

                ResidualBlock(

                    hidden_dim=
                        config.hidden_dim,

                    expansion_dim=
                        config.expansion_dim,

                    dropout=
                        config.dropout,

                    residual_dropout=
                        config.residual_dropout

                )

                for _ in range(
                    config.residual_blocks
                )
            )
        )


        self.output_norm = (
            nn.LayerNorm(
                config.hidden_dim
            )
        )


        self.head = (
            nn.Sequential(

                nn.Linear(
                    config.hidden_dim,
                    config.head_dim
                ),

                nn.SiLU(),

                nn.Dropout(
                    config.dropout
                ),

                nn.Linear(
                    config.head_dim,
                    config.num_classes
                )
            )
        )


    def forward(
        self,
        x
    ):

        if (
            x.ndim
            != 2
        ):

            raise ValueError(
                "Neural network input "
                "must have shape "
                "(batch, features)."
            )


        if (
            x.shape[1]
            != self.config.input_dim
        ):

            raise ValueError(
                "Neural network received "
                f"{x.shape[1]} features; "
                f"expected "
                f"{self.config.input_dim}."
            )


        x = (
            self.feature_gate(
                x
            )
        )


        x = (
            self.input_projection(
                x
            )
        )


        x = (
            self.input_norm(
                x
            )
        )


        x = (
            self.input_activation(
                x
            )
        )


        for block in self.blocks:

            x = (
                block(
                    x
                )
            )


        x = (
            self.output_norm(
                x
            )
        )


        logits = (
            self.head(
                x
            )
        )


        return (
            logits
        )


    def feature_gates(
        self,
        x
    ):

        return (
            self.feature_gate
            .get_gates(
                x
            )
        )


def count_trainable_parameters(
    model
):

    return sum(

        parameter.numel()

        for parameter
        in model.parameters()

        if parameter.requires_grad
    )
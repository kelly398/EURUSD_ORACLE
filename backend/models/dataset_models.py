from sqlalchemy import (
    Column,
    Integer,
    String,
    Float,
    DateTime,
    JSON,
    UniqueConstraint,
    Index
)

from backend.database.models import Base

class MLTrainingSample(
    Base
):

    __tablename__ = (
        "ml_training_samples"
    )


    id = Column(
        Integer,
        primary_key=True
    )


    symbol = Column(
        String,
        nullable=False
    )


    timeframe = Column(
        String,
        nullable=False
    )


    feature_version = Column(
        String,
        nullable=False
    )


    label_version = Column(
        String,
        nullable=False
    )

    dataset_version = Column(
        String,
        nullable=False
    )

    feature_timestamp = Column(
        DateTime(timezone=True),
        nullable=False
    )


    target_timestamp = Column(
        DateTime(timezone=True),
        nullable=False
    )


    target_move_pips = Column(
        Float,
        nullable=False
    )


    target_label = Column(
        String,
        nullable=False
    )


    feature_json = Column(
        JSON,
        nullable=False
    )


    split = Column(
        String,
        nullable=False
    )


    __table_args__ = (

        UniqueConstraint(
            "symbol",
            "timeframe",
            "feature_timestamp",
            "feature_version",
            "dataset_version",
            "label_version",
            name="uq_ml_training_sample",
        ),
        Index(
            "ix_ml_training_samples_training_slice",
            "symbol",
            "timeframe",
            "feature_version",
            "dataset_version",
            "label_version",
            "split",
            "feature_timestamp",
        ),
    )

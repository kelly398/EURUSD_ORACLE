from backend.database.connection import engine

from backend.database.models import Base

from backend.models.dataset_models import MLTrainingSample

def initialize():

    Base.metadata.create_all(
        bind=engine
    )


if __name__ == "__main__":

    initialize()

    print(
        "DATABASE TABLES CREATED"
    )
    
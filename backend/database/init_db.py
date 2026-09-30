from backend.database.connection import engine

from backend.database.models import Base


def initialize():

    Base.metadata.create_all(
        bind=engine
    )


if __name__ == "__main__":

    initialize()

    print(
        "DATABASE TABLES CREATED"
    )
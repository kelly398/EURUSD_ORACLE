from sqlalchemy import text

from backend.database.connection import engine


def main():

    try:

        with engine.connect() as connection:

            result = connection.execute(
                text("SELECT version();")
            )

            print(
                result.fetchone()
            )


        print(
            "DATABASE CONNECTION SUCCESS"
        )


    except Exception as e:

        print(
            "DATABASE CONNECTION FAILED"
        )

        print(e)



if __name__ == "__main__":

    main()
from backend.config.settings import (
    PROJECT_NAME,
    VERSION,
    ENVIRONMENT
)


def main():

    print("=" * 40)

    print(PROJECT_NAME)

    print(f"Version: {VERSION}")

    print(f"Environment: {ENVIRONMENT}")

    print("SYSTEM ONLINE")

    print("=" * 40)


if __name__ == "__main__":
    main()

"""
A minimal, interactive tour of Syncwave.

It will create two simple Pydantic models (`Customer` and `Order`) that are kept in-sync
with two JSON files on disk. Type a command shown in the menu to play with the data and
immediately see the effect of your changes both in memory *and* on disk.

You are encouraged to open `data/customers.json` and `data/orders.json` in another
editor while the script is running; edit them, save, and watch Syncwave detect and
reload the changes on-the-fly.
"""

from __future__ import annotations

import random
import string
from pathlib import Path

from pydantic import BaseModel

from syncwave import Syncwave
from syncwave.io import io

# ---------------------------------------------------------------------------
# Model and demo setup
# ---------------------------------------------------------------------------


syncwave = Syncwave(data_dir="data")


@syncwave.register(name="orders", key="id")
class Order(BaseModel):
    id: int
    product_id: int
    quantity: int


@syncwave.register(name="customers", key="id")
class Customer(BaseModel):
    id: int
    name: str
    age: int
    orders: list[int] = []


def create_initial_data() -> None:
    Order(id=1, product_id=1, quantity=10)
    Order(id=2, product_id=2, quantity=5)
    Order(id=3, product_id=1, quantity=20)

    Customer(id=1, name="Jane Doe", age=28, orders=[1, 2])
    Customer(id=2, name="John Doe", age=31, orders=[3])


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------


def next_customer_id() -> int:
    store = syncwave["customers"]
    return (max(store.keys()) if store else 0) + 1


def random_name() -> str:
    """Generate a random two-word name."""
    first = "".join(random.choices(string.ascii_letters, k=random.randint(4, 8)))
    last = "".join(random.choices(string.ascii_letters, k=random.randint(4, 8)))
    return f"{first.title()} {last.title()}"


def random_age() -> int:
    return random.randint(18, 90)


def print_in_memory() -> None:
    print("\nIn-memory state:\n" + str(syncwave) + "\n")


def print_files() -> None:
    customers_path = Path("data/customers.json").resolve()
    orders_path = Path("data/orders.json").resolve()

    print(f"\n{customers_path}:")
    print(io.json_dumps(io.read_json(customers_path)))

    print(f"\n{orders_path}:")
    print(io.json_dumps(io.read_json(orders_path)))
    print()


MENU = """
Enter a command and press <Enter>:
    p        print in-memory data
    f        print JSON files on disk
    a        add 1 random customer
    a-N      add N random customers (e.g. a-10)
    o        show this options menu again
    q        quit
"""

# ---------------------------------------------------------------------------
# Main interactive loop
# ---------------------------------------------------------------------------


def main() -> None:
    create_initial_data()

    print(MENU)

    while True:
        choice = input("Command > ").strip().lower()

        if choice == "o":
            print(MENU)
            continue

        if choice == "p":
            print_in_memory()
            continue

        if choice == "f":
            print_files()
            continue

        if choice.startswith("a"):
            if choice == "a":
                n = 1
            else:
                try:
                    n = int(choice.split("-")[1])
                except (ValueError, IndexError):
                    print("Invalid number, using 1.")
                    n = 1

            for _ in range(max(1, n)):
                Customer(
                    id=next_customer_id(),
                    name=random_name(),
                    age=random_age(),
                )
            print(f"Added {n} customer{'s' if n != 1 else ''}.")
            continue

        if choice == "q":
            print("\nStopping Syncwave")
            syncwave.stop()
            print("Good-bye!")
            break

        print("Unknown command. Type 'o' to show the options again.")


if __name__ == "__main__":
    main()

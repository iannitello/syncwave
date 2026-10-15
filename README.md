# Syncwave

Turn plain JSON files into a live data store, two-way synced with your Python objects.

## Documentation

Read the docs at https://syncwave.dev/.

A few useful links:

- [Cheat Sheet](https://syncwave.dev/cheat_sheet/)
- [Usage Guide](https://syncwave.dev/usage/syncwave/)
- [API Reference](https://syncwave.dev/api/syncwave/)

## Installation

Install from [PyPI](https://pypi.org/project/syncwave/).

```shell
# uv
uv add syncwave

# pip
pip install syncwave
```

## What Is Syncwave

Syncwave keeps in-memory Python objects and JSON files synchronized, in both directions:

- Change your data from Python (an `append`, a dict assignment, a model field update) and the JSON file is updated.
- Edit the JSON file, by hand or from another process, and your Python objects are updated in place.
- Every change, from either side, is validated with [Pydantic](https://pydantic.dev/docs/validation/latest/get-started/) against the store's declared type.

You get persistence with _zero_ setup: no database to install, no ORM, no serialization code. Your data lives in JSON files you can open and edit at any time.

If you know Python type hints and Pydantic models, you already know everything you need.

Requires Python 3.10+.

## Versioning

Syncwave is under active development, and until version 1.0, new releases may introduce backward-incompatible changes.

## License

Syncwave is licensed under the MIT License.

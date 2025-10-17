from __future__ import annotations

from pydantic import BaseModel

from syncwave import Syncwave

syncwave = Syncwave(data_dir="data")


# 1. The usual way to register a model is to use the decorator:
@syncwave.register(name="customers", key="id")
class Customer(BaseModel):
    id: int
    name: str
    age: int
    orders: list[int] = []


# At this point, the class is not '__main__.Customer', but actually 'syncwave.Customer'.
# The original Customer is just a intermediary class that the new one inherits from.
# It makes sense to keep the original name for the new class because
# a user creating a Customer instance with `customer = Customer(...)`
# would not expect the type of `customer` to be "SyncCustomer".


# 2. You can also create a model and make it reactive later:
class Order(BaseModel):
    id: int
    product_id: int
    quantity: int


SyncOrder = syncwave.make_reactive(Order)
syncwave.register(SyncOrder, name="orders", key="id")

# Here, Order is still '__main__.Order', but we also have `syncwave.SyncOrder`.
# In that case, it makes sense to use a different name for the new class because
# you really could use Order and SyncOrder differently.

order1 = Order(id=1, product_id=1, quantity=10)  # a normal, non-reactive instance
order2 = SyncOrder(id=2, product_id=2, quantity=5)  # a reactive instance


# 3. Finally, you can also use your own name for the class:
class Address(BaseModel):
    id: int
    street: str
    city: str
    state: str
    zip: str


SavedAddress = syncwave.make_reactive(Address, class_name="SavedAddress")
syncwave.register(SavedAddress, name="addresses", key="id")

# will be saved to data/addresses.json
address1 = SavedAddress(id=1, street="123 Main", city="Town", state="CA", zip="12345")

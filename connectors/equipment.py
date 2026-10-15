"""
connectors/equipment.py — Mock hardware logistics client.

Simulates laptop ordering, shipment tracking, and address updates.
In production, replace with your hardware vendor API (e.g., Firstbase, Hofy, or internal IT).
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from connectors.base import BaseConnector
from core.models.hire_state import HireState, Address, EquipmentStatus


class EquipmentConnector(BaseConnector):
    """Mock equipment / hardware logistics connector."""

    SECTION = "equipment_orders"

    def order_laptop(self, hire: HireState) -> str:
        """
        Place a laptop order for a new hire.
        Returns the order ID.
        """
        orders = self._get_section(self.SECTION)
        order_id = f"ORDER-{hire.hire_id[:8].upper()}"

        orders[hire.hire_id] = {
            "order_id":       order_id,
            "hire_id":        hire.hire_id,
            "recipient_name": hire.full_legal_name,
            "laptop_model":   hire.laptop_model,
            "address":        hire.shipping_address.model_dump(),
            "status":         EquipmentStatus.ORDERED.value,
            "ordered_at":     datetime.utcnow().isoformat(),
            "shipped_at":     None,
            "delivered_at":   None,
            "cancelled_at":   None,
            "tracking_number": None,
        }
        self._set_section(self.SECTION, orders)
        print(
            f"  [Equipment] ✅ Laptop ordered: {order_id} — "
            f"{hire.laptop_model} → {hire.shipping_address.one_line()}"
        )
        return order_id

    def update_shipping_address(self, hire_id: str, new_address: Address) -> bool:
        """
        Update the shipping address for a pending (not yet shipped) order.
        Returns True on success, False if order cannot be changed (already shipped).
        """
        orders = self._get_section(self.SECTION)
        if hire_id not in orders:
            print(f"  [Equipment] ❌ No order found for hire: {hire_id}")
            return False

        order = orders[hire_id]
        if order["status"] in {EquipmentStatus.SHIPPED.value, EquipmentStatus.DELIVERED.value}:
            print(
                f"  [Equipment] ❌ Cannot update address — "
                f"order is already {order['status']}: {order['order_id']}"
            )
            return False

        order["address"] = new_address.model_dump()
        order["address_updated_at"] = datetime.utcnow().isoformat()
        self._set_section(self.SECTION, orders)
        print(f"  [Equipment] ✅ Address updated for order {order['order_id']}: {new_address.one_line()}")
        return True

    def cancel_order(self, hire_id: str) -> bool:
        """
        Cancel a laptop order.
        Returns True on success, False if already shipped/delivered.
        """
        orders = self._get_section(self.SECTION)
        if hire_id not in orders:
            return False

        order = orders[hire_id]
        if order["status"] == EquipmentStatus.DELIVERED.value:
            print(f"  [Equipment] ❌ Cannot cancel — order already DELIVERED: {order['order_id']}")
            return False

        order["status"] = EquipmentStatus.CANCELLED.value
        order["cancelled_at"] = datetime.utcnow().isoformat()
        self._set_section(self.SECTION, orders)
        print(f"  [Equipment] ⚠️  Order cancelled: {order['order_id']}")
        return True

    def mark_shipped(self, hire_id: str, tracking_number: str) -> bool:
        """Simulate carrier picking up the package (used in test scenarios)."""
        orders = self._get_section(self.SECTION)
        if hire_id not in orders:
            return False
        orders[hire_id]["status"] = EquipmentStatus.SHIPPED.value
        orders[hire_id]["shipped_at"] = datetime.utcnow().isoformat()
        orders[hire_id]["tracking_number"] = tracking_number
        self._set_section(self.SECTION, orders)
        print(f"  [Equipment] 📦 Order marked SHIPPED: tracking={tracking_number}")
        return True

    def get_order(self, hire_id: str) -> Optional[dict]:
        """Fetch the current order record for a hire."""
        return self._get_section(self.SECTION).get(hire_id)

    def get_status(self, hire_id: str) -> Optional[EquipmentStatus]:
        """Return just the current order status enum value."""
        order = self.get_order(hire_id)
        if not order:
            return None
        return EquipmentStatus(order["status"])

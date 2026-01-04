"""Mesh Nodes Generic"""
import logging
import asyncio

from bluetooth_mesh import models
from mesh import Node
from mesh.composition import Composition, Element


def on_message(source, destination, app_index, message):
    timestamp = datetime.now().strftime("%Y-%m-%d %T.%f")
    print(f"{timestamp} {source:04x} -> {destination:04x}: {message!r}")

class Generic(Node):
    """
    Generic Bluetooth Mesh node

    Provides additional functionality compared to the very basic Node class,
    like composition model helpers and node configuration.
    """

    OnlineProperty = "online"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # stores the node's composition data
        self._composition = None
        # lists all bound model
        self._bound_models = set()

    def _is_model_bound(self, model):
        """
        Check if the given model is supported and bound
        """
        return model in self._bound_models

    async def fetch_composition(self):
        client = self._app.elements[0][models.ConfigClient]
        data = await client.get_composition_data([self.unicast], net_index=0, timeout=30)
        logging.info(f"Fetch composition: {data}")

        node_data = (data or {}).get(self.unicast)
        if not isinstance(node_data, dict):
            logging.warning(f"Composition fetch failed for {self.unicast:04x}: {data}")
            self._composition = None
            return

        page_zero = node_data.get("zero")
        if not page_zero:
            logging.warning(f"Composition page zero missing for {self.unicast:04x}: {node_data}")
            self._composition = None
            return

        self._composition = Composition(page_zero)

    async def bind(self, app):
        await super().bind(app)

        # update the composition data
        await self.fetch_composition()

        logging.debug(f"Node composition:\n{self._composition}")

    async def bind_model(self, model):
        """
        Bind the given model to the application key
        """
        if self._composition is None:
            logging.warning(f"No composition for {self}; force-binding {model}")
        else:
            element = self._composition.element(0)
            if not element.supports(model):
                logging.info(f"{self} does not support {model}")
                return False

        client = self._app.elements[0][models.ConfigClient]
        await client.bind_app_key(
            self.unicast,
            net_index=0,
            element_address=self.unicast,
            app_key_index=self._app.app_keys[0][0],
            model=model
        )
        self._bound_models.add(model)
        logging.info(f"{self} bound {model}")
        return True
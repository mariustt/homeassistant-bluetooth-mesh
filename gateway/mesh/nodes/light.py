"""Mesh Nodes Light"""
import asyncio
import logging

from bluetooth_mesh import models
from bluetooth_mesh.messages import LightLightnessOpcode

from .generic import Generic

# https://developer.nordicsemi.com/nRF_Connect_SDK/doc/1.9.99-dev1/nrf/libraries/bluetooth_services/mesh/light_ctl_srv.html?highlight=65535%20light#states
BLE_MESH_MIN_LIGHTNESS = 0
BLE_MESH_MAX_LIGHTNESS = 65535
BLE_MESH_MIN_TEMPERATURE = 800  # Kelvin
BLE_MESH_MAX_TEMPERATURE = 20000  # Kelvin
BLE_MESH_MIN_MIRED = 50
BLE_MESH_MAX_MIRED = 1250


class Light(Generic):
    """
    Generic interface for light nodes

    Tracks the available feature of the light. Currently supports
        - GenericOnOffServer
            - turn on and off
        - LightLightnessServer
            - set brightness
        - LightCTLServer
            - set color temperature

    For now only a single element is supported.
    """

    OnOffProperty = "onoff"
    BrightnessProperty = "brightness"
    TemperatureProperty = "temperature"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self._features = set()
        self._availability_failures = 0
        self._availability_state = None
        self._availability_threshold = max(1, self.config.optional("availability_failures", 3))

    def _mired_limits(self):
        min_mired = int(self.config.optional("mireds_min", BLE_MESH_MIN_MIRED))
        max_mired = int(self.config.optional("mireds_max", BLE_MESH_MAX_MIRED))
        if min_mired > max_mired:
            logging.warning(f"{self}: swapping mired limits ({min_mired} > {max_mired})")
            min_mired, max_mired = max_mired, min_mired
        min_mired = max(1, min_mired)
        max_mired = max(min_mired, max_mired)
        return min_mired, max_mired

    def _kelvin_limits(self):
        min_mired, max_mired = self._mired_limits()
        min_kelvin = max(BLE_MESH_MIN_TEMPERATURE, int(1e6 / max_mired))
        max_kelvin = min(BLE_MESH_MAX_TEMPERATURE, int(1e6 / min_mired))
        if min_kelvin > max_kelvin:
            min_kelvin, max_kelvin = max_kelvin, min_kelvin
        return min_kelvin, max_kelvin

    def _clamp_kelvin(self, kelvin):
        min_kelvin, max_kelvin = self._kelvin_limits()
        if kelvin < min_kelvin:
            return min_kelvin
        if kelvin > max_kelvin:
            return max_kelvin
        return kelvin

    def kelvin_to_mireds(self, kelvin):
        min_mired, max_mired = self._mired_limits()
        if kelvin <= 0:
            return max_mired
        mired = int(round(1e6 / kelvin))
        if mired < min_mired:
            return min_mired
        if mired > max_mired:
            return max_mired
        return mired

    def supports(self, property):  # pylint: disable=redefined-builtin
        logging.debug(f"Supports: {self._features}")
        return property in self._features

    def _availability_success(self):
        self._availability_failures = 0
        if self._availability_state != "online":
            self._availability_state = "online"
            self.notify("availability", "online")

    def _availability_failure(self):
        self._availability_failures += 1
        if self._availability_failures >= self._availability_threshold:
            if self._availability_state != "offline":
                self._availability_state = "offline"
                self.notify("availability", "offline")

    async def turn_on(self, ack=False):
        if not ack:
            await self.set_onoff_unack(True)
        else:
            await self.set_onoff(True)

    async def refresh(self):
        await self.ready.wait()
        while True:
            await self.get_availability()
            await asyncio.sleep(60)

    def lightness_cb(self, source: int,
            net_index: int,
            destination,
            message):
        if (self.unicast == source):
            self._availability_success()
            self.notify(Light.BrightnessProperty, message["light_lightness_status"]["present_lightness"])


    async def turn_off(self, ack=False):
        if not ack:
            await self.set_onoff_unack(False)
        else:
            await self.set_onoff(False)

    async def set_brightness(self, brightness, ack=False):
        if self._is_model_bound(models.LightLightnessServer):
            if not ack:
                await self.set_lightness_unack(brightness)
            else:
                await self.set_lightness(brightness)

    async def kelvin(self, temperature, ack=False):
        if self._is_model_bound(models.LightCTLServer):
            logging.info(f"{temperature} Kelvin")
            if not ack:
                await self.set_ctl_unack(temperature=temperature)
            else:
                await self.set_ctl(temperature=temperature)

    async def mireds_to_kelvin(self, temperature, ack=False, is_tuya=False):
        if self._is_model_bound(models.LightCTLServer):
            kelvin = max(1, 1000000 // temperature)
            kelvin = self._clamp_kelvin(kelvin)
            logging.info(f"{temperature} mired = {kelvin} Kelvin")
            if not ack:
                await self.set_ctl_unack(temperature=kelvin, is_tuya=is_tuya)
            else:
                await self.set_ctl(temperature=kelvin, is_tuya=is_tuya)

    def kelvin_to_tuya_level(self, kelvin: int) -> int:
        """
        Map real kelvin range (derived from mireds_min/max) to mesh CTL temperature range [800..20000].
        Intended for Tuya-style devices that expect CTL temperature in that normalized range.
        """
        min_kelvin, max_kelvin = self._kelvin_limits()
        kelvin = self._clamp_kelvin(kelvin)
        if max_kelvin == min_kelvin:
            return BLE_MESH_MIN_TEMPERATURE

        tuya_level = (
            (kelvin - min_kelvin)
            * (BLE_MESH_MAX_TEMPERATURE - BLE_MESH_MIN_TEMPERATURE)
            / (max_kelvin - min_kelvin)
            + BLE_MESH_MIN_TEMPERATURE
        )
        return int(round(tuya_level))

    async def bind(self, app):
        await super().bind(app)

        if await self.bind_model(models.GenericOnOffServer):
            self._features.add(Light.OnOffProperty)
            await self.get_onoff()

        if await self.bind_model(models.LightLightnessServer):
            self._features.add(Light.OnOffProperty)
            self._features.add(Light.BrightnessProperty)
            await self.get_lightness()
            await self.get_lightness_range()

        if await self.bind_model(models.LightCTLServer):
            self._features.add(Light.TemperatureProperty)
            self._features.add(Light.BrightnessProperty)
            await self.get_ctl()
            await self.get_light_temperature_range()

        client = self._app.elements[0][models.LightLightnessClient]
        client.app_message_callbacks[LightLightnessOpcode.LIGHT_LIGHTNESS_STATUS] \
            .add(self.lightness_cb)

    async def refresh_state(self):
        if self.supports(Light.OnOffProperty):
            await self.get_onoff()
        if self.supports(Light.BrightnessProperty):
            await self.get_lightness()
        if self.supports(Light.TemperatureProperty):
            await self.get_ctl()

    async def set_onoff_unack(self, onoff, **kwargs):
        self.notify(Light.OnOffProperty, onoff)
        client = self._app.elements[0][models.GenericOnOffClient]
        await client.set_onoff_unack(self.unicast, self._app.app_keys[0][0], onoff, **kwargs)

    async def set_onoff(self, onoff, **kwargs):
        self.notify(Light.OnOffProperty, onoff)
        client = self._app.elements[0][models.GenericOnOffClient]
        await client.set_onoff(self.unicast, self._app.app_keys[0][0], onoff, **kwargs)

    async def get_availability(self):
        client = self._app.elements[0][models.GenericOnOffClient]
        try:
            state = await client.get_light_status([self.unicast], self._app.app_keys[0][0])
        except Exception as err:  # pylint: disable=broad-except
            logging.warning(f"Availability poll failed for {self}: {err}")
            self._availability_failure()
            return

        result = (state or {}).get(self.unicast)
        if result is None or isinstance(result, BaseException):
            logging.warning(f"Received invalid availability result for {self}: {state}")
            self._availability_failure()
            return

        self._availability_success()

    async def get_onoff(self):
        client = self._app.elements[0][models.GenericOnOffClient]
        state = await client.get_light_status([self.unicast], self._app.app_keys[0][0])

        result = state[self.unicast]
        if result is None:
            logging.warning(f"Received invalid result {state}")
        elif not isinstance(result, BaseException):
            logging.info(f"Get OnOff: {state}")
            self.notify(Light.OnOffProperty, result["present_onoff"])

    async def set_lightness_unack(self, lightness, **kwargs):
        if lightness > BLE_MESH_MAX_LIGHTNESS:
            lightness = BLE_MESH_MAX_LIGHTNESS
        self.notify(Light.BrightnessProperty, lightness)

        client = self._app.elements[0][models.LightLightnessClient]
        await client.set_lightness_unack(
            destination=self.unicast, app_index=self._app.app_keys[0][0], lightness=lightness, **kwargs
        )

    async def set_lightness(self, lightness, **kwargs):
        if lightness > BLE_MESH_MAX_LIGHTNESS:
            lightness = BLE_MESH_MAX_LIGHTNESS
        self.notify(Light.BrightnessProperty, lightness)

        client = self._app.elements[0][models.LightLightnessClient]
        await client.set_lightness([self.unicast], app_index=self._app.app_keys[0][0], lightness=lightness, **kwargs)

    async def get_lightness(self):
        client = self._app.elements[0][models.LightLightnessClient]
        state = await client.get_lightness([self.unicast], self._app.app_keys[0][0])

        result = state[self.unicast]
        if result is None:
            logging.warning(f"Received invalid result {state}")
        elif not isinstance(result, BaseException):
            logging.info(f"Get Lightness: {state}")
            self.notify(Light.BrightnessProperty, result["present_lightness"])

    async def get_lightness_range(self):
        client = self._app.elements[0][models.LightLightnessClient]
        state = await client.get_lightness_range([self.unicast], self._app.app_keys[0][0])

        result = state[self.unicast]
        if result is None:
            logging.warning(f"Received invalid result {state}")
        elif not isinstance(result, BaseException):
            logging.info(f"Get Lightness Range: {state}")

    async def set_ctl_unack(self, temperature=None, brightness=None, is_tuya=False, **kwargs):
        if temperature:
            temperature = self._clamp_kelvin(temperature)
        if brightness and brightness > BLE_MESH_MAX_LIGHTNESS:
            brightness = BLE_MESH_MAX_LIGHTNESS

        if temperature:
            self.notify(Light.TemperatureProperty, temperature)
        else:
            temperature = self.retained(Light.TemperatureProperty, BLE_MESH_MAX_TEMPERATURE)

        if brightness:
            self.notify(Light.BrightnessProperty, brightness)
        else:
            brightness = self.retained(Light.BrightnessProperty, BLE_MESH_MAX_LIGHTNESS)

        ctl_temperature = self.kelvin_to_tuya_level(temperature) if is_tuya else temperature

        if is_tuya:
            logging.debug(f"{self} -> Tuya CTL {temperature}K => {ctl_temperature}")

        client = self._app.elements[0][models.LightCTLClient]
        await client.set_ctl_unack(
            destination=self.unicast,
            app_index=self._app.app_keys[0][0],
            ctl_temperature=ctl_temperature,
            ctl_lightness=brightness,
            **kwargs,
        )

    async def set_ctl(self, temperature=None, is_tuya=False, **kwargs):
        if temperature:
            temperature = self._clamp_kelvin(temperature)

        if temperature:
            self.notify(Light.TemperatureProperty, temperature)
        else:
            temperature = self.retained(Light.TemperatureProperty, BLE_MESH_MAX_TEMPERATURE)

        ctl_temperature = self.kelvin_to_tuya_level(temperature) if is_tuya else temperature

        client = self._app.elements[0][models.LightCTLClient]
        await client.set_ctl([self.unicast], self._app.app_keys[0][0], ctl_temperature=ctl_temperature, **kwargs)

    async def get_ctl(self):
        client = self._app.elements[0][models.LightCTLClient]
        state = await client.get_ctl([self.unicast], self._app.app_keys[0][0])

        result = state[self.unicast]
        if result is None:
            logging.warning(f"Received invalid result {state}")
        elif not isinstance(result, BaseException):
            logging.info(f"Get CTL: {state}")

    async def get_light_temperature_range(self):
        client = self._app.elements[0][models.LightCTLClient]
        state = await client.get_light_temperature_range([self.unicast], self._app.app_keys[0][0])

        result = state[self.unicast]
        if result is None:
            logging.warning(f"Received invalid result {state}")
        elif not isinstance(result, BaseException):
            logging.info(f"Get Light Temperature Range: {state}")

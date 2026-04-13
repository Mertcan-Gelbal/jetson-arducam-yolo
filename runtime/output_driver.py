import logging


log = logging.getLogger("visiondock.runtime")


class BaseOutputDriver:
    def __init__(self, profile: dict):
        self.profile = profile or {}
        self.state = {}

    def setup(self):
        return True

    def set_signal(self, signal_name: str, enabled: bool):
        self.state[signal_name] = bool(enabled)

    def apply_decision(self, decision: str):
        """Assert GPIO/mock signal lines for the given inspection decision.

        Decision → output pin mapping:
          pass      → pass=HIGH, all others LOW
          fail      → fail=HIGH, all others LOW
          uncertain → fault=HIGH  (operator review required)
          fault     → fault=HIGH, all others LOW
          busy      → busy=HIGH, all others LOW
          idle/ready → all LOW (safe resting state)
        """
        decision = (decision or "").strip().lower()
        _DECISION_OUTPUTS = {
            "pass":      {"pass": True,  "fail": False, "fault": False, "busy": False},
            "fail":      {"pass": False, "fail": True,  "fault": False, "busy": False},
            "uncertain": {"pass": False, "fail": False, "fault": True,  "busy": False},
            "fault":     {"pass": False, "fail": False, "fault": True,  "busy": False},
            "busy":      {"pass": False, "fail": False, "fault": False, "busy": True},
            "idle":      {"pass": False, "fail": False, "fault": False, "busy": False},
            "ready":     {"pass": False, "fail": False, "fault": False, "busy": False},
        }
        outputs = _DECISION_OUTPUTS.get(decision)
        if outputs is None:
            raise ValueError(
                f"Unsupported decision: {decision!r}. "
                f"Valid values: {sorted(_DECISION_OUTPUTS)}"
            )
        for key, value in outputs.items():
            self.set_signal(key, value)
        log.debug("apply_decision %s → %s", decision, outputs)
        return {"decision": decision, "state": dict(self.state)}

    def cleanup(self):
        return None


class MockOutputDriver(BaseOutputDriver):
    pass


class JetsonGPIOOutputDriver(BaseOutputDriver):
    def __init__(self, profile: dict):
        super().__init__(profile)
        self.GPIO = None
        self.active_high = True
        self._pin_map = {}

    def setup(self):
        try:
            import Jetson.GPIO as GPIO
        except ImportError as exc:
            raise RuntimeError("Jetson.GPIO is not installed on this target.") from exc

        gpio_cfg = (self.profile or {}).get("gpio", {})
        self.active_high = str(gpio_cfg.get("active_level") or "high").lower() != "low"
        self.GPIO = GPIO
        GPIO.setwarnings(False)
        GPIO.setmode(GPIO.BOARD)

        for signal_name, pin_key in (
            ("pass", "pass_pin"),
            ("fail", "fail_pin"),
            ("fault", "fault_pin"),
            ("busy", "busy_pin"),
        ):
            pin = gpio_cfg.get(pin_key)
            if pin is None:
                continue
            GPIO.setup(int(pin), GPIO.OUT, initial=self._inactive_level())
            self._pin_map[signal_name] = int(pin)
        return True

    def _active_level(self):
        return self.GPIO.HIGH if self.active_high else self.GPIO.LOW

    def _inactive_level(self):
        return self.GPIO.LOW if self.active_high else self.GPIO.HIGH

    def set_signal(self, signal_name: str, enabled: bool):
        super().set_signal(signal_name, enabled)
        pin = self._pin_map.get(signal_name)
        if pin is None or self.GPIO is None:
            return
        self.GPIO.output(pin, self._active_level() if enabled else self._inactive_level())

    def cleanup(self):
        if self.GPIO is not None:
            try:
                self.GPIO.cleanup()
            except Exception as exc:
                log.warning("GPIO cleanup failed: %s", exc)

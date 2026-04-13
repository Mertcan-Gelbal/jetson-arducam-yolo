import logging
import threading
import time

log = logging.getLogger("visiondock.runtime")

class JetsonGPIOInputDriver:
    """
    Listens for hardware triggers (e.g. a foot pedal or PLC signal) on a Jetson GPIO pin.
    Invokes the provided callback when a valid trigger edge is detected.
    """
    def __init__(self, profile: dict, trigger_callback):
        self.profile = profile or {}
        self.trigger_callback = trigger_callback
        self.GPIO = None
        self._pin = None
        self._running = False
        self._thread = None
        self._debounce_time = 0.3  # 300ms debounce
        self._last_trigger_time = 0

    def setup(self):
        gpio_cfg = self.profile.get("gpio", {})
        if not gpio_cfg.get("enabled"):
            return False

        pin = gpio_cfg.get("trigger_pin")
        if pin is None:
            return False

        try:
            import Jetson.GPIO as GPIO
        except ImportError as exc:
            log.warning("Jetson.GPIO is not installed. Hardware trigger disabled.")
            return False

        self.GPIO = GPIO
        self._pin = int(pin)
        
        # Determine edge (default to falling edge for typical pull-up circuits)
        edge_str = str(gpio_cfg.get("trigger_edge") or "falling").lower()
        if edge_str == "rising":
            self._edge = GPIO.RISING
            pull_up_down = GPIO.PUD_DOWN
        elif edge_str == "both":
            self._edge = GPIO.BOTH
            pull_up_down = GPIO.PUD_UP
        else:
            self._edge = GPIO.FALLING
            pull_up_down = GPIO.PUD_UP

        GPIO.setwarnings(False)
        GPIO.setmode(GPIO.BOARD)
        
        try:
            GPIO.setup(self._pin, GPIO.IN, pull_up_down=pull_up_down)
        except (RuntimeError, OSError, ValueError) as e:
            log.error(f"Failed to setup trigger pin {self._pin}: {e}")
            return False

        self._running = True
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True, name="HardwareTrigger")
        self._thread.start()
        log.info(f"Hardware trigger listening on pin {self._pin} ({edge_str} edge)")
        return True

    def _monitor_loop(self):
        """
        We use polling with GPIO.wait_for_edge because Jetson.GPIO add_event_detect 
        callbacks can sometimes cause issues with complex multithreading in Python.
        """
        while self._running and self.GPIO is not None:
            try:
                # Wait for edge with a timeout so we can gracefully exit on shutdown
                edge_detected = self.GPIO.wait_for_edge(self._pin, self._edge, timeout=500)
                if edge_detected is not None and self._running:
                    now = time.time()
                    if (now - self._last_trigger_time) > self._debounce_time:
                        self._last_trigger_time = now
                        log.debug("Hardware trigger detected!")
                        # Dispatch the callback in a separate thread so we don't block the listener
                        threading.Thread(target=self._safe_callback, daemon=True).start()
            except (RuntimeError, OSError, ValueError) as e:
                log.error(f"Error in hardware trigger monitor loop: {e}")
                time.sleep(1)

    def _safe_callback(self):
        if self.trigger_callback:
            try:
                self.trigger_callback(source="gpio_trigger")
            except (RuntimeError, OSError, TypeError, ValueError) as e:
                log.error(f"Trigger callback failed: {e}")

    def cleanup(self):
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        # Cleanup is handled globally or via output_driver since Jetson.GPIO.cleanup() cleans all pins
        # We just remove our event listener if using event_detect, but here we just break the loop.


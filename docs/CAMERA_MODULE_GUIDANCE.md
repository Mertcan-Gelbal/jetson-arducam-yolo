# Camera Module Guidance

This product should treat camera hardware in three separate layers:

1. **Sensor family**: IMX219, IMX477, IMX519
2. **Module SKU**: fixed-focus, manual-focus, motorized-focus, PTZ, IR-cut, etc.
3. **Lens package**: stock lens, M12 lens, CS/C lens, zoom lens, manual iris lens

Do not collapse these into one operator dropdown. The same sensor family can exist in multiple module and lens variants with different software capabilities.

## What the sources say

- NVIDIA Jetson uses **Libargus** as the camera capture API and it is built around explicit frame captures and sensor requests, which is the correct production path for CSI cameras on Jetson.
  Source: [NVIDIA Libargus API](https://docs.nvidia.com/jetson/archives/r36.4/ApiReference/group__LibargusAPI.html)
- Raspberry Pi’s official **High Quality Camera** brief for **IMX477** says the board is an interchangeable-lens platform with **M12** or **CS/C-mount** variants and a focus adjustment ring on the CS mount. That means focus and aperture are often lens-side, not sensor-side.
  Source: [Raspberry Pi High Quality Camera Product Brief](https://datasheets.raspberrypi.com/hq-camera/hq-camera-product-brief.pdf)
- Arducam’s official Jetson pages for **IMX219** list multiple SKUs on the same sensor including **manual focus**, **fixed focus**, **motorized focus**, and even **pan-tilt-zoom** kits.
  Source: [Arducam IMX219 for Jetson](https://docs.arducam.com/Nvidia-Jetson-Camera/Native-Camera/imx219/)
- Arducam’s official Jetson page for **IMX477** lists **manual-focus**, **motorized-focus**, and **zoom/PTZ** variants on the same sensor family.
  Source: [Arducam IMX477 for Jetson](https://docs.arducam.com/Nvidia-Jetson-Camera/Native-Camera/imx477/)
- Arducam’s official Jetson page for **IMX519** describes a 16MP module family where **autofocus** stock-lens modules are common, but it still does not imply generic zoom or iris control.
  Source: [Arducam IMX519 for Jetson](https://docs.arducam.com/Nvidia-Jetson-Camera/Native-Camera/imx519/)
- Arducam’s Jetson motorized-focus quick start includes an **IMX219 autofocus** flow. That is the clearest proof that focus capability cannot be derived from the sensor name alone.
  Source: [Arducam Jetson Motorized Focus Quick Start](https://docs.arducam.com/Nvidia-Jetson-Camera/Motorized-Focus-Camera/quick-start/)

## Product decisions

- Daily operators should not choose `IMX219 / IMX477 / IMX519`.
- Sensor family belongs to commissioning metadata and runtime validation.
- The Jetson runtime should report the detected sensor family and warn when it does not match the saved commissioning metadata.
- Focus control should appear only when the installed module has a supported motorized focuser.
- Zoom should not appear unless the product explicitly supports a zoom-lens or PTZ kit.
- Aperture should not appear unless the product explicitly supports an electronically controlled iris, which is not the default for the modules currently targeted here.

## Recommendation for the sock inspection station

- Use a fixed mechanical setup.
- Prefer a locked stock lens or a manually adjusted industrial lens.
- Set focus, framing, exposure, and lighting during commissioning.
- Keep those values locked for operators.
- Let the operator see only runtime health, trigger, last decision, and result history.

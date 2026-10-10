# Dashboard first-screen loading

- Integrate upstream #10413's Monaco, locale, ApexCharts, and response compression optimizations.
- Keep VAD scripts commented out and leave the local VAD implementation unchanged; this branch does not enable VAD or dynamically download its dependencies.
- Preserve every existing locale module, including local Agent Teams translations, while loading only the selected language.
- Preserve Monaco language support, workers, and existing editor bindings while deferring the editor bundle until first use.
- Keep static asset compression and caching behavior, honor `gzip;q=0` and Range requests, and leave SSE events uncompressed and unbuffered.

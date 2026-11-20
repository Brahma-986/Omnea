function initSwaggerUI() {
  window.ui = SwaggerUIBundle({
    url: "/openapi.json",
    dom_id: "#swagger-ui",
    deepLinking: true,
    presets: [
      SwaggerUIBundle.presets.apis,
      SwaggerUIStandalonePreset
    ],
    plugins: [
      SwaggerUIBundle.plugins.DownloadUrl
    ],
    layout: "StandaloneLayout"
  });
}

if (document.readyState === "loading") {
  window.addEventListener("DOMContentLoaded", initSwaggerUI);
} else {
  initSwaggerUI();
}

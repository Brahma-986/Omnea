// Self-hosted external script for Admin Review actions (strict CSP compliant, zero inline script)
document.addEventListener("DOMContentLoaded", function () {
  document.querySelectorAll(".btn-resolve-task").forEach(function (btn) {
    btn.addEventListener("click", async function () {
      const taskId = btn.getAttribute("data-task-id");
      const action = btn.getAttribute("data-action");
      const token = document.querySelector('meta[name="auth-token"]')?.getAttribute("content") || "";
      const tenantId = document.querySelector('meta[name="tenant-id"]')?.getAttribute("content") || "";
      try {
        const res = await fetch("/api/v1/reviews/tasks/" + taskId + "/resolve", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "Authorization": "Bearer " + token,
            "x-tenant-id": tenantId,
          },
          body: JSON.stringify({ action: action }),
        });
        if (res.ok) {
          window.location.reload();
        } else {
          const err = await res.json();
          alert("Resolution failed: " + (err.detail || "Error"));
        }
      } catch (e) {
        alert("Request error: " + e);
      }
    });
  });
});

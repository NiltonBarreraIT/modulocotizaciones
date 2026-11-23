(() => {
  document.querySelectorAll("table").forEach((table) => {
    if (table.classList.contains("commercial-lines-table")) {
      table.classList.add("mobile-scroll-table");
      return;
    }

    const headers = [...table.querySelectorAll("thead th")].map((header) =>
      header.textContent.trim()
    );
    if (!headers.length) return;

    table.classList.add("mobile-card-table");
    table.closest(".scroll")?.classList.add("mobile-card-table-wrap");

    table.querySelectorAll("tbody tr").forEach((row) => {
      const cells = [...row.children].filter((cell) => cell.tagName === "TD");
      const isEmptyRow = cells.length === 1 && Number(cells[0].colSpan) > 1;
      if (isEmptyRow) {
        row.classList.add("mobile-empty-row");
        return;
      }
      cells.forEach((cell, index) => {
        cell.dataset.label = headers[index] || "Detalle";
      });
    });
  });
})();

(() => {
  const revisionUrl = document.body.dataset.platformRevisionUrl;
  let revision = Number.parseInt(document.body.dataset.platformRevision || "0", 10);
  if (!revisionUrl || !Number.isFinite(revision)) return;

  let hasUnsavedChanges = false;
  let isNavigating = false;
  let updateNotice;

  const isEditableField = (element) =>
    element.matches("input:not([type='hidden']):not([readonly]), select, textarea");
  document.addEventListener("input", (event) => {
    if (isEditableField(event.target)) hasUnsavedChanges = true;
  });
  document.addEventListener("change", (event) => {
    if (isEditableField(event.target)) hasUnsavedChanges = true;
  });
  document.addEventListener("submit", () => {
    isNavigating = true;
  });

  const showUpdateNotice = () => {
    if (updateNotice) return;
    updateNotice = document.createElement("div");
    updateNotice.className = "flash";
    updateNotice.innerHTML =
      "Hay cambios nuevos en la plataforma. " +
      '<button type="button" style="margin-left:8px">Actualizar ahora</button>';
    updateNotice.querySelector("button").addEventListener("click", () => location.reload());
    document.querySelector("main")?.prepend(updateNotice);
  };

  const checkForUpdates = async () => {
    if (isNavigating) return;
    try {
      const response = await fetch(revisionUrl, {
        cache: "no-store",
        headers: { Accept: "application/json" },
      });
      if (!response.ok) return;
      const latest = Number.parseInt((await response.json()).revision, 10);
      if (!Number.isFinite(latest) || latest <= revision) return;
      revision = latest;
      if (hasUnsavedChanges) {
        showUpdateNotice();
      } else {
        isNavigating = true;
        location.reload();
      }
    } catch (_error) {
      // A temporary connection failure is retried on the next interval.
    }
  };

  window.setInterval(checkForUpdates, 4000);
})();

(() => {
  const statusForm = document.querySelector("[data-commercial-status-form]");
  if (!statusForm) return;
  const status = statusForm.querySelector("[data-commercial-status]");
  const reason = statusForm.querySelector("[data-status-reason]");
  const reasonLabel = statusForm.querySelector("[data-status-reason-label]");
  const currentStatus = statusForm.dataset.currentStatus;
  const syncRequirement = () => {
    const isAwarded = status.value === "Adjudicada";
    reason.required = status.value !== currentStatus;
    reasonLabel.textContent = isAwarded ? "Orden de compra" : "Justificación del cambio";
    reason.placeholder = isAwarded
      ? "Ingrese orden de compra"
      : "Ingrese la justificación del cambio";
  };
  status.addEventListener("change", syncRequirement);
  syncRequirement();
})();

(() => {
  const form = document.querySelector("[data-commercial-form]");
  if (!form) return;

  const rows = () => [...form.querySelectorAll("[data-item]")];
  const number = (element, grouped = false) => {
    let text = (element?.value || "0").trim().replaceAll(" ", "");
    if (text.includes(",") && text.includes(".")) {
      const decimal = text.lastIndexOf(",") > text.lastIndexOf(".") ? "," : ".";
      const grouping = decimal === "," ? "." : ",";
      text = text.replaceAll(grouping, "").replace(decimal, ".");
    } else if (grouped && /^[+-]?\d{1,3}([.,]\d{3})+$/.test(text)) {
      text = text.replaceAll(".", "").replaceAll(",", "");
    } else if ((text.match(/,/g) || []).length === 1) {
      text = text.replace(",", ".");
    } else if (text.includes(",")) {
      text = text.replaceAll(",", "");
    }
    const value = Number.parseFloat(text);
    return Number.isFinite(value) ? value : 0;
  };
  const amount = (value, currency) =>
    new Intl.NumberFormat("es-CL", {
      minimumFractionDigits: currency === "CLP" ? 0 : 2,
      maximumFractionDigits: currency === "CLP" ? 0 : 2,
    }).format(value);
  const recalculate = () => {
    let subtotal = 0;
    const currency = form.querySelector("[data-currency]").value;
    rows().forEach((row) => {
      const quantity = number(row.querySelector("[data-quantity]"));
      const price = number(row.querySelector("[data-price]"), true);
      const discount = number(row.querySelector("[data-discount]"));
      const total = quantity * price * (1 - discount / 100);
      row.querySelector("[data-line-total]").textContent = amount(total, currency);
      subtotal += total;
    });
    const globalDiscount = number(form.querySelector("[data-global-discount]"));
    const discountTotal = subtotal * globalDiscount / 100;
    const netTotal = subtotal - discountTotal;
    form.querySelector("[data-live-totals]").textContent =
      `Subtotal ${currency} ${amount(subtotal, currency)} · ` +
      `Descuento ${amount(discountTotal, currency)} · ` +
      `Total neto ${amount(netTotal, currency)}`;
  };

  form.addEventListener("input", recalculate);
  form.addEventListener("click", (event) => {
    const addButton = event.target.closest("[data-add-item]");
    if (addButton) {
      const template = form.querySelector("[data-item-template]");
      form.querySelector("[data-items]").append(template.content.cloneNode(true));
      recalculate();
      return;
    }
    const removeButton = event.target.closest("[data-remove-item]");
    if (removeButton) {
      removeButton.closest("[data-item]").remove();
      recalculate();
    }
  });
  recalculate();
})();

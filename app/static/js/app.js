/* Shell behaviour: dark-mode + sidebar-collapse persisted via cookie,
   DataTables/Select2 auto-init. */
(function () {
  function setCookie(name, value) {
    document.cookie = name + "=" + value + ";path=/;max-age=31536000;SameSite=Lax";
  }
  function getCookie(name) {
    const m = document.cookie.match("(^|;)\\s*" + name + "\\s*=\\s*([^;]+)");
    return m ? m.pop() : "";
  }

  // Dark mode
  const savedTheme = getCookie("fms-theme");
  if (savedTheme) document.documentElement.setAttribute("data-bs-theme", savedTheme);
  const darkToggle = document.getElementById("darkModeToggle");
  if (darkToggle) {
    darkToggle.addEventListener("click", function () {
      const html = document.documentElement;
      const next = html.getAttribute("data-bs-theme") === "dark" ? "light" : "dark";
      html.setAttribute("data-bs-theme", next);
      setCookie("fms-theme", next);
    });
  }

  // Sidebar collapse
  const wrapper = document.getElementById("fms-wrapper");
  if (wrapper && getCookie("fms-sidebar") === "collapsed") {
    wrapper.classList.add("sidebar-collapsed");
  }
  const sbToggle = document.getElementById("sidebarToggle");
  if (sbToggle) {
    sbToggle.addEventListener("click", function () {
      wrapper.classList.toggle("sidebar-collapsed");
      setCookie("fms-sidebar",
        wrapper.classList.contains("sidebar-collapsed") ? "collapsed" : "open");
    });
  }

  // Sidebar category groups: remember collapsed/expanded state per group
  document.querySelectorAll(".sidebar-group-toggle").forEach(function (btn) {
    var targetId = btn.getAttribute("data-bs-target");
    var target = document.querySelector(targetId);
    if (!target) return;
    var stateKey = "fms-sbgroup-" + targetId.replace("#", "");
    var saved = getCookie(stateKey);
    if (saved === "collapsed") {
      target.classList.remove("show");
      btn.setAttribute("aria-expanded", "false");
    } else {
      btn.setAttribute("aria-expanded", "true");
    }
    target.addEventListener("shown.bs.collapse", function () {
      setCookie(stateKey, "expanded");
      btn.setAttribute("aria-expanded", "true");
    });
    target.addEventListener("hidden.bs.collapse", function () {
      setCookie(stateKey, "collapsed");
      btn.setAttribute("aria-expanded", "false");
    });
  });

  // Auto-init DataTables and Select2 when jQuery is present
  if (window.jQuery) {
    jQuery(function ($) {
      $("table.fms-datatable").DataTable({ pageLength: 25 });
      $("select.fms-select2").select2({ width: "100%", theme: "default" });
    });
  }

  // Notification bell: poll unread count + load recent on open
  function loadNotifications() {
    fetch("/admin/notifications/recent")
      .then(function(r){ return r.json(); })
      .then(function(data) {
        var items = document.getElementById("notifItems");
        if (!items) return;
        if (!data.notifications || data.notifications.length === 0) {
          items.innerHTML = '<li class="px-3 py-2 text-muted small">No notifications</li>';
          return;
        }
        items.innerHTML = data.notifications.map(function(n) {
          return '<li><a class="dropdown-item py-2 ' + (n.is_read ? 'text-muted' : 'fw-semibold') + '" href="#" data-id="' + n.id + '">' +
            '<div class="small">' + n.title + '</div>' +
            '<div class="text-muted" style="font-size:.78rem">' + n.message + '</div></a></li>';
        }).join('');
        items.querySelectorAll("a[data-id]").forEach(function(a) {
          a.addEventListener("click", function(e) {
            e.preventDefault();
            fetch("/admin/notifications/" + a.dataset.id + "/mark-read", {method:"POST",headers:{"X-CSRFToken": getCsrfToken()}});
            a.classList.remove("fw-semibold");
          });
        });
      }).catch(function(){});
  }

  function updateBadge() {
    fetch("/admin/notifications/unread-count")
      .then(function(r){ return r.json(); })
      .then(function(data) {
        var badge = document.getElementById("notifBadge");
        if (!badge) return;
        if (data.count > 0) {
          badge.textContent = data.count > 99 ? "99+" : data.count;
          badge.classList.remove("d-none");
        } else {
          badge.classList.add("d-none");
        }
      }).catch(function(){});
  }

  function getCsrfToken() {
    var meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.getAttribute("content") : "";
  }

  var bell = document.getElementById("notifBell");
  if (bell) {
    bell.addEventListener("show.bs.dropdown", loadNotifications);
    updateBadge();
    setInterval(updateBadge, 60000);
    var markAll = document.getElementById("markAllRead");
    if (markAll) {
      markAll.addEventListener("click", function(e) {
        e.preventDefault();
        fetch("/admin/notifications/mark-all-read", {method:"POST",headers:{"X-CSRFToken":getCsrfToken()}})
          .then(function(){ updateBadge(); loadNotifications(); });
      });
    }
  }

  // Attachment panels: AJAX upload + delete, no page reload/JSON dump
  function escapeHtml(s) {
    var d = document.createElement("div");
    d.textContent = s;
    return d.innerHTML;
  }

  function renderAttachmentRow(att) {
    var thumb = att.is_image
      ? '<a href="' + att.view_url + '" target="_blank"><img src="' + att.view_url +
        '" alt="" style="height:32px;width:32px;object-fit:cover;border-radius:4px;" class="me-2"></a>'
      : '<i class="bi bi-file-earmark me-2"></i>';
    var viewBtn = att.is_image
      ? '<a class="btn btn-sm btn-outline-secondary" target="_blank" href="' + att.view_url + '"><i class="bi bi-eye"></i></a> '
      : '';
    return '<li class="list-group-item d-flex justify-content-between align-items-center" data-attachment-id="' + att.id + '">' +
      '<span>' + thumb + escapeHtml(att.filename) +
      '<span class="text-muted small ms-2">' + (att.size / 1024).toFixed(1) + ' KB</span></span>' +
      '<div>' + viewBtn +
      '<a class="btn btn-sm btn-outline-secondary" href="' + att.download_url + '"><i class="bi bi-download"></i></a> ' +
      '<button type="button" class="btn btn-sm btn-outline-danger fms-attachment-delete" data-attachment-id="' + att.id + '"><i class="bi bi-trash"></i></button>' +
      '</div></li>';
  }

  document.querySelectorAll(".fms-attachment-upload-form").forEach(function (form) {
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      var card = form.closest(".card");
      var list = card.querySelector(".fms-attachment-list");
      var errorBox = form.querySelector(".fms-upload-error");
      var btn = form.querySelector("button[type=submit]");
      var spinner = btn.querySelector(".spinner-border");
      var label = btn.querySelector(".upload-label");
      errorBox.classList.add("d-none");
      btn.disabled = true;
      spinner.classList.remove("d-none");
      label.textContent = "Uploading...";

      var formData = new FormData(form);
      fetch("/master/attachments/upload", {
        method: "POST",
        body: formData,
        headers: {"X-CSRFToken": getCsrfToken()}
      })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          btn.disabled = false;
          spinner.classList.add("d-none");
          label.textContent = "Upload";
          if (!data.ok) {
            errorBox.textContent = data.error || "Upload failed.";
            errorBox.classList.remove("d-none");
            return;
          }
          var emptyRow = list.querySelector("li.text-muted");
          if (emptyRow) emptyRow.remove();
          list.insertAdjacentHTML("beforeend", renderAttachmentRow(data));
          form.reset();
        })
        .catch(function () {
          btn.disabled = false;
          spinner.classList.add("d-none");
          label.textContent = "Upload";
          errorBox.textContent = "Upload failed. Please try again.";
          errorBox.classList.remove("d-none");
        });
    });
  });

  document.addEventListener("click", function (e) {
    var delBtn = e.target.closest(".fms-attachment-delete");
    if (!delBtn) return;
    e.preventDefault();
    var id = delBtn.dataset.attachmentId;
    Swal.fire({title: "Delete this attachment?", icon: "warning",
               showCancelButton: true, confirmButtonText: "Yes"})
      .then(function (result) {
        if (!result.isConfirmed) return;
        fetch("/master/attachments/" + id + "/delete", {
          method: "POST", headers: {"X-CSRFToken": getCsrfToken()}
        }).then(function (r) { return r.json(); })
          .then(function (data) {
            if (data.ok) {
              var row = document.querySelector('li[data-attachment-id="' + id + '"]');
              if (row) row.remove();
            }
          });
      });
  });
})();

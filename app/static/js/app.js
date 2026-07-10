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
    var m = document.cookie.match("(^|;)\\s*csrf_token\\s*=\\s*([^;]+)");
    return m ? m.pop() : "";
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
})();

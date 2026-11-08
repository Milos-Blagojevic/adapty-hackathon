/* Shared helpers for the design screens (*.dc.html): JSON API calls and the formats the design uses.
 * ?mock=1 → a screen keeps the design's own sample data and makes no request.
 * The API is the same origin (served by `python -m resolve`); ?api=http://host:port overrides it.
 */
(function () {
  'use strict';
  var params = new URLSearchParams(location.search);
  var MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

  function request(method, path, body) {
    var base = (params.get('api') || '').replace(/\/$/, '');
    var opts = { method: method, headers: {} };
    if (body !== undefined) {
      opts.headers['content-type'] = 'application/json';
      opts.body = JSON.stringify(body);
    }
    return fetch(base + path, opts).then(function (r) {
      return r.text().then(function (t) {
        var data = null;
        try { data = t ? JSON.parse(t) : null; } catch (e) { data = t; }
        if (!r.ok) {
          var d = data && data.detail;
          var msg = typeof d === 'string' ? d : Array.isArray(d) ? d.map(function (x) { return x.msg; }).join('; ') : 'HTTP ' + r.status;
          var err = new Error(msg);
          err.status = r.status;
          throw err;
        }
        return data;
      });
    });
  }

  function parse(ts) { return ts ? new Date(ts) : null; }

  window.API = {
    mock: params.get('mock') === '1',
    param: function (k) { return params.get(k); },
    get: function (path) { return request('GET', path); },
    post: function (path, body) { return request('POST', path, body === undefined ? {} : body); },
    put: function (path, body) { return request('PUT', path, body); },
    /** '4m', '1h', '2d' while recent; 'Nov 6' after a week (Incoming rows). */
    ago: function (ts, now) {
      var d = parse(ts);
      if (!d) return '';
      var s = Math.max(0, ((now || Date.now()) - d.getTime()) / 1000);
      if (s < 60) return 'now';
      if (s < 3600) return Math.floor(s / 60) + 'm';
      if (s < 86400) return Math.floor(s / 3600) + 'h';
      if (s < 7 * 86400) return Math.floor(s / 86400) + 'd';
      return MONTHS[d.getMonth()] + ' ' + d.getDate();
    },
    /** '21:40' (local time). */
    hm: function (ts) {
      var d = parse(ts);
      if (!d) return '';
      return String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0');
    },
    /** '21:39:12' (local time). */
    hms: function (ts) {
      var d = parse(ts);
      if (!d) return '';
      return [d.getHours(), d.getMinutes(), d.getSeconds()].map(function (n) { return String(n).padStart(2, '0'); }).join(':');
    },
    /** 'Nov 20' / 'Nov 20 2026' with year. */
    day: function (ts, withYear) {
      var d = parse(ts);
      if (!d) return '';
      return MONTHS[d.getMonth()] + ' ' + d.getDate() + (withYear ? ' ' + d.getFullYear() : '');
    },
    /** '4m 18s', '1h 00m', '0m 52s' (Analyze live tasks). */
    duration: function (sec) {
      sec = Math.max(0, Math.floor(sec || 0));
      var h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
      return h ? h + 'h ' + String(m).padStart(2, '0') + 'm' : m + 'm ' + String(s).padStart(2, '0') + 's';
    }
  };
})();

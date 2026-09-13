/* Minis Bridge dashboard plugin — background deliverer.
 *
 * Polls the plugin backend (/api/plugins/minis-bridge/pending), which proxies
 * the Mac mini collector. For each event:
 *   claim (atomic lease) → submit into the origin session via v2 RPC
 *   → confirm deliver (or release on failure/busy).
 * The collector token never leaves the backend; this file holds no secrets.
 * Registers a header-right chip for visibility; no tab of its own.
 */
(function () {
  'use strict';

  var SDK = window.__HERMES_PLUGIN_SDK__;
  if (!SDK || !window.__HERMES_PLUGINS__) return;
  var React = SDK.React;
  var fetchJSON = SDK.fetchJSON;

  var API = '/api/plugins/minis-bridge';
  var POLL_MS = 5000;

  // Tiny shared store for chip rendering.
  var listeners = [];
  var state = { pending: 0, status: 'init', last: '' };
  function setState(patch) {
    for (var k in patch) state[k] = patch[k];
    listeners.forEach(function (fn) { fn(state); });
  }

  function formatEvent(ev) {
    if (ev.kind === 'push') {
      return '[来自 Minis｜push:' + ev.request_id + ']\n\n' + ev.body +
        '\n\n——以上是 Minis 主动发来的消息，请阅读后按内容回应。';
    }
    return '[Minis 回复送达｜request_id=' + ev.request_id + ']\n\n' + ev.body +
      '\n\n——以上是 Minis 对此会话先前发出任务的处理结果，请基于它继续。';
  }

  function post(path, body) {
    return fetchJSON(API + path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {}),
    });
  }

  var processing = false;

  function deliverOne(ev) {
    return post('/claim', { event_id: ev.event_id }).then(function (claim) {
      if (!claim || claim.ok !== true) return 'lost-claim'; // another tab got it
      var target = ev.session_id
        ? Promise.resolve(ev.session_id)
        : post('/ensure-inbox', {}).then(function (r) {
            return r && r.status === 'ok' ? r.session_id : null;
          });
      return target.then(function (sessionId) {
        if (!sessionId) {
          return post('/release', { event_id: ev.event_id }).then(function () { return 'no-session'; });
        }
        return post('/submit', { session_id: sessionId, text: formatEvent(ev) }).then(function (r) {
          if (r && r.status === 'submitted') {
            return post('/deliver', { event_id: ev.event_id }).then(function (d) {
              return d && d.ok === true ? 'delivered' : 'confirm-failed';
            });
          }
          // busy or error → release the lease, retry on a later poll
          return post('/release', { event_id: ev.event_id }).then(function () {
            return r && r.status === 'busy' ? 'busy' : 'error';
          });
        });
      });
    });
  }

  function tick() {
    if (processing) return;
    fetchJSON(API + '/pending').then(function (data) {
      var events = data && Array.isArray(data.events) ? data.events : [];
      setState({
        pending: events.length,
        status: data && data.configured === false ? 'unconfigured' : (data && data.error ? 'offline' : 'ok'),
      });
      if (!events.length || processing) return;
      processing = true;
      var chain = Promise.resolve();
      events.forEach(function (ev) {
        chain = chain.then(function () {
          return deliverOne(ev).then(function (outcome) {
            if (outcome === 'delivered') {
              setState({ last: '已送达 ' + (ev.session_id || '收件箱') });
            } else if (outcome === 'busy') {
              setState({ last: '目标会话忙，稍后重试' });
              throw { stop: true }; // keep order; retry next tick
            } else if (outcome === 'lost-claim') {
              // another tab handled it; keep going
            } else {
              setState({ last: '投递失败:' + outcome });
              throw { stop: true };
            }
          });
        });
      });
      return chain.catch(function (e) { if (!e || !e.stop) setState({ last: String(e) }); })
        .then(function () {
          processing = false;
          // refresh count after processing
          return fetchJSON(API + '/pending').then(function (d2) {
            var evs = d2 && Array.isArray(d2.events) ? d2.events : [];
            setState({ pending: evs.length });
          });
        });
    }).catch(function (err) {
      setState({ status: 'offline', last: String(err && err.message || err) });
    });
  }

  function Chip() {
    var pair = React.useState(state);
    React.useEffect(function () {
      var fn = function (next) { pair[1](Object.assign({}, next)); };
      listeners.push(fn);
      return function () { listeners = listeners.filter(function (f) { return f !== fn; }); };
    }, []);
    var cur = pair[0] || state;
    var label = 'Minis';
    if (cur.status === 'unconfigured') label = 'Minis·未配置';
    else if (cur.status === 'offline') label = 'Minis·离线';
    else if (cur.pending > 0) label = 'Minis·' + cur.pending;
    return React.createElement('span', {
      className: 'text-xs text-muted-foreground',
      title: 'Minis Bridge｜待投递 ' + cur.pending + (cur.last ? '｜' + cur.last : ''),
    }, label);
  }

  window.__HERMES_PLUGINS__.register('minis-bridge', function () { return null; });
  try {
    window.__HERMES_PLUGINS__.registerSlot('minis-bridge', 'header-right', Chip);
  } catch (e) { /* slot support optional */ }

  setInterval(tick, POLL_MS);
  setTimeout(tick, 1500); // first run shortly after load
})();

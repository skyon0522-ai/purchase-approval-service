'use strict';
const $ = id => document.getElementById(id);
const token = document.querySelector('meta[name="console-token"]').content;
let current = null, busy = false, uncertain = false, pendingDecision = null;
const money = cents => 'USD ' + (cents / 100).toFixed(2);

function controls() {
  const matchingID = $('request-id').value === current?.request_id;
  document.querySelectorAll('button').forEach(button => button.disabled = busy);
  $('revise').disabled = busy || uncertain || !matchingID || current?.status !== 'pending';
  $('review').disabled = busy || uncertain || !matchingID || current?.status !== 'pending';
  $('refresh').disabled = busy || !current;
  $('create').disabled = busy || uncertain;
}
function details(element, values) {
  element.replaceChildren();
  for (const [label, value] of values) {
    const term = document.createElement('dt'), description = document.createElement('dd');
    term.textContent = label; description.textContent = String(value); element.append(term, description);
  }
}
async function api(command, value) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 25000);
  let response, result;
  try {
    response = await fetch('/api/' + command, {method: 'POST',
      headers: {'Content-Type': 'application/json', 'X-Console-Token': token},
      body: JSON.stringify(value), signal: controller.signal});
    result = await response.json();
  } catch {
    const error = new Error('The result is not confirmed. Reload the request before another action.');
    error.unknown = true;
    throw error;
  } finally { clearTimeout(timeout); }
  if (!response.ok) {
    const error = new Error(result.error || 'The request did not complete.');
    error.unknown = result.outcome === 'unknown';
    error.reload = response.status === 409 && ['decide', 'revise'].includes(command);
    throw error;
  }
  return result;
}
async function perform(action, message) {
  if (busy) return;
  busy = true; controls(); $('error').hidden = true;
  try { await action(); $('feedback').textContent = message; }
  catch (error) {
    if (error.unknown || error.reload) uncertain = true;
    $('error').textContent = error.message; $('error').hidden = false; $('error').focus();
    $('feedback').textContent = 'No automatic retry was made. Your draft is still on this page.';
  } finally { busy = false; controls(); }
}
function display(result, fillDraft = true) {
  current = result.request; uncertain = false;
  $('state').textContent = `Loaded ${current.request_id}: ${current.status} · Temporal ${result.temporal_status}`;
  $('current').hidden = false;
  details($('current'), [['Request ID', current.request_id], ['Revision', current.revision],
    ['Item', current.item], ['Quantity', current.quantity], ['Unit price', money(current.unit_price_cents)],
    ['Total', money(current.quantity * current.unit_price_cents)], ['Actor label', current.actor || 'Not decided'],
    ['Mock order', current.order_id || 'Not released']]);
  if (fillDraft) {
    $('request-id').value = current.request_id; $('item').value = current.item;
    $('quantity').value = current.quantity; $('unit-price').value = current.unit_price_cents; draftTotal();
  }
}
function draftTotal() {
  const quantity = Number($('quantity').value), cents = Number($('unit-price').value);
  $('draft-total').textContent = Number.isSafeInteger(quantity) && Number.isSafeInteger(cents) && quantity > 0 && cents > 0
    ? 'Draft total: ' + money(quantity * cents) : 'Enter a quantity and unit price in integer cents.';
}
const purchase = () => ({request_id: $('request-id').value, item: $('item').value,
  quantity: Number($('quantity').value), unit_price_cents: Number($('unit-price').value)});
async function orders() {
  const result = await api('orders', {}); $('orders').replaceChildren();
  for (const order of result.orders) {
    const row = document.createElement('tr');
    for (const value of [order.request_id, order.item, order.revision, money(order.total_cents), order.order_id]) {
      const cell = document.createElement('td'); cell.textContent = String(value); row.append(cell);
    }
    $('orders').append(row);
  }
  $('orders-status').textContent = `${result.orders.length} mock supplier order(s). No real supplier was called.`;
}
$('purchase-form').addEventListener('submit', event => {
  event.preventDefault(); const value = purchase();
  perform(async () => display(await api('submit', value)), 'Request created. Inspect its current revision.');
});
$('load').addEventListener('click', () => perform(async () => display(await api('status', {request_id: $('request-id').value})), 'Request loaded from Temporal.'));
$('refresh').addEventListener('click', () => perform(async () => {
  display(await api('status', {request_id: current.request_id}), false); await orders();
}, 'Current state reloaded. Unsaved draft fields were kept.'));
$('revise').addEventListener('click', () => {
  if (!$('purchase-form').reportValidity() || !current) return;
  const value = {...purchase(), request_id: current.request_id, expected_revision: current.revision};
  perform(async () => display(await api('revise', value)), 'New revision saved. Review this revision before deciding.');
});
$('decision-form').addEventListener('submit', event => {
  event.preventDefault(); if (!current || uncertain || busy) return;
  pendingDecision = {request_id: current.request_id, expected_revision: current.revision,
    actor: $('actor').value, decision: $('decision').value};
  details($('confirmation-details'), [['Decision', pendingDecision.decision], ['Request', current.request_id],
    ['Revision', current.revision], ['Item', current.item], ['Quantity', current.quantity],
    ['Total', money(current.quantity * current.unit_price_cents)], ['Actor label', pendingDecision.actor]]);
  $('confirm').textContent = `${pendingDecision.decision[0].toUpperCase() + pendingDecision.decision.slice(1)} revision ${current.revision}`;
  $('confirmation').showModal(); $('back').focus();
});
$('back').addEventListener('click', () => {$('confirmation').close(); pendingDecision = null;});
$('confirmation').addEventListener('cancel', () => {pendingDecision = null;});
$('confirm').addEventListener('click', () => {
  if (!pendingDecision || busy) return;
  const value = pendingDecision; pendingDecision = null; $('confirmation').close();
  perform(async () => {display(await api('decide', value), false); await orders();},
    'Decision accepted. Reload the request to confirm its final state and mock order.');
});
$('orders-refresh').addEventListener('click', () => perform(orders, 'Mock supplier orders refreshed.'));
for (const id of ['quantity', 'unit-price']) $(id).addEventListener('input', draftTotal);
$('request-id').addEventListener('input', controls);
controls();

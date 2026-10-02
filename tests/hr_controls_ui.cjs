// Contract tests for Frappe Desk controls without a live server.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const root = path.join(__dirname, '..', 'maas_attendance_mobile', 'maas_attendance_mobile');

function harness(file) {
  const nodes = [], handlers = {}, routes = [], buttons = [];
  const frappe = {
    pages: {'maas-attendance-controls': {}}, user_roles: ['System Manager'], session: {user: 'hr@test'},
    ui: {make_app_page: () => ({main: {}, set_secondary_action() {}}), form: {on: (name, value) => handlers[name] = value}},
    set_route: (...args) => routes.push(args), confirm: (_message, action) => action(),
  };
  const context = {
    frappe, __: (text, args = []) => text.replace(/\{(\d+)\}/g, (_, index) => args[index]),
    $: tag => {
      const node = {tag, value: '', click: null, text(value) {this.value = value; return this;}, on(_event, fn) {this.click = fn; return this;}, appendTo() {return this;}, empty() {nodes.length = 0; return this;}, append() {return this;}};
      nodes.push(node); return node;
    },
  };
  vm.runInNewContext(fs.readFileSync(path.join(root, file), 'utf8'), context);
  return {frappe, nodes, handlers, routes, buttons};
}
const data = {api_version: 1, test_mode: true, can_edit_settings: false, test_employee: '<script>employee</script>', policy: {}, counts: {pending_devices: 2, approved_devices: 1, pending_claims: 3, enabled_workplaces: 1}};

test('HR overview renders employee as text and opens filtered pending claims', async () => {
  const h = harness('page/maas_attendance_controls/maas_attendance_controls.js');
  h.frappe.call = async () => ({message: data});
  const wrapper = {};
  h.frappe.pages['maas-attendance-controls'].on_page_load(wrapper);
  await wrapper.refresh_controls();
  assert.ok(h.nodes.some(node => node.value.includes('<script>employee</script>')));
  assert.ok(!h.nodes.some(node => node.value === 'Edit attendance settings'));
  h.nodes.find(node => node.value === 'Offline claims awaiting review (3)').click();
  assert.equal(h.routes[0][1], 'Mobile Attendance Offline Claim');
  assert.equal(h.frappe.route_options.status, 'Pending Review');
});

test('failed refresh replaces previous dashboard with an actionable error', async () => {
  const h = harness('page/maas_attendance_controls/maas_attendance_controls.js');
  h.frappe.call = async () => ({message: data});
  const wrapper = {}; h.frappe.pages['maas-attendance-controls'].on_page_load(wrapper);
  await wrapper.refresh_controls();
  h.frappe.call = async () => {throw new Error('private server details');};
  await wrapper.refresh_controls();
  assert.ok(h.nodes.some(node => node.value.startsWith('Cannot load attendance controls')));
  assert.ok(!h.nodes.some(node => node.value === 'Punch records and photos'));
  assert.ok(!h.nodes.some(node => node.value.includes('private server details')));
});

test('disable action changes both submission gates and saves through the form', async () => {
  const h = harness('doctype/mobile_attendance_settings/mobile_attendance_settings.js');
  let values, saves = 0;
  const frm = {add_custom_button: (label, callback) => h.buttons.push({label, callback}), set_value: async value => values = value, save: async () => saves++};
  h.handlers['Mobile Attendance Settings'].refresh(frm);
  await h.buttons.find(button => button.label === 'Disable test submissions').callback();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(values.enable_test_attendance, 0); assert.equal(values.enable_offline_test, 0);
  assert.equal(saves, 1);
});

test('device review hides self/revoked devices and saves eligible approval', async () => {
  const h = harness('doctype/mobile_attendance_device/mobile_attendance_device.js');
  let values, saves = 0;
  const frm = {is_new: () => false, doc: {user: 'hr@test', status: 'Pending'}, add_custom_button: (label, callback) => h.buttons.push({label, callback}), set_value: async (...value) => values = value, save: async () => saves++};
  const refresh = h.handlers['Mobile Attendance Device'].refresh;
  refresh(frm); assert.equal(h.buttons.length, 0);
  frm.doc.user = 'employee@test'; frm.doc.status = 'Revoked';
  refresh(frm); assert.equal(h.buttons.length, 0);
  frm.doc.status = 'Pending'; refresh(frm);
  await h.buttons.find(button => button.label === 'Approve device').callback();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(values[0], 'status'); assert.equal(values[1], 'Approved'); assert.equal(saves, 1);
});

test('photo hold uses scoped API, reason prompt and reload, without saving evidence', async () => {
  const h = harness('../public/js/photo_retention.js');
  let args, reloads = 0;
  h.frappe.prompt = (_fields, action) => action({reason: 'Keep for review'});
  h.frappe.call = async request => {args = request; return {message: {hold: true}};};
  h.frappe.msgprint = () => assert.fail('Unexpected hold error');
  const frm = {is_new: () => false, doc: {name: 'ID', punch_photo: '/private/files/photo.jpg'},
    add_custom_button: (label, callback) => h.buttons.push({label, callback}), reload_doc: async () => reloads++};
  h.handlers['Mobile Attendance Transaction'].refresh(frm);
  h.buttons[0].callback(); await new Promise(resolve => setImmediate(resolve));
  assert.equal(args.method, 'maas_attendance_mobile.api.photo_retention_hold');
  assert.equal(args.type, 'POST'); assert.equal(args.args.hold, 1);
  assert.equal(args.args.name, 'ID'); assert.equal(args.args.reason, 'Keep for review');
  assert.equal(reloads, 1);
  h.buttons.length = 0; frm.doc.photo_removed_at = '2026-10-02';
  h.handlers['Mobile Attendance Transaction'].refresh(frm); assert.equal(h.buttons.length, 0);
});

test('photo retention preview shows counts and does not enable cleanup', async () => {
  const h = harness('doctype/mobile_attendance_company_policy/mobile_attendance_company_policy.js');
  let request, message;
  h.frappe.call = async value => {request = value; return {message: {api_version: 1, company: 'A',
    automatic_cleanup_enabled: false, counts: {eligible: 2, held: 1, awaiting_review: 3, kept: 4}}};};
  h.frappe.msgprint = value => message = value;
  const frm = {is_new: () => false, doc: {company: 'A'}, add_custom_button: (label, callback) => h.buttons.push({label, callback})};
  h.handlers['Mobile Attendance Company Policy'].refresh(frm);
  await h.buttons[0].callback();
  assert.equal(request.type, 'GET'); assert.equal(request.args.company, 'A');
  assert.ok(message.message.includes('Eligible photos: 2'));
  assert.ok(message.message.includes('Automatic cleanup is disabled.'));
});

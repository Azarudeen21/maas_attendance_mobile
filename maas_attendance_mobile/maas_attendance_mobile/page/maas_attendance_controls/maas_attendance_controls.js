frappe.pages['maas-attendance-controls'].on_page_load = function(wrapper) {
  const page = frappe.ui.make_app_page({parent: wrapper, title: __('MAAS Attendance Controls'), single_column: true});
  const body = $('<div class="p-4"></div>').appendTo(page.main);
  let generation = 0;
  const route = (doctype, filters = {}) => {
    frappe.route_options = filters;
    frappe.set_route('List', doctype);
  };
  const button = (parent, label, callback) => $('<button type="button" class="btn btn-default mr-2 mb-2"></button>')
    .text(__(label)).on('click', callback).appendTo(parent);
  wrapper.refresh_controls = async () => {
    const request = ++generation;
    body.empty().append($('<p></p>').text(__('Loading attendance controls…')));
    try {
      const response = await frappe.call({method: 'maas_attendance_mobile.api.hr_overview', type: 'GET'});
      if (request !== generation) return;
      const data = response.message;
      if (data?.api_version !== 1 || data.test_mode !== true) throw new Error('Invalid overview');
      body.empty();
      $('<div class="alert alert-info"></div>').text(__('Test attendance only. Every punch skips automatic attendance. Offline uploads require HR approval. Photos are evidence; face identity and liveness are not verified.')).appendTo(body);
      $('<h4></h4>').text(__('Current policy')).appendTo(body);
      $('<p></p>').text(__('Test employee: {0}', [data.test_employee || __('Not selected')])).appendTo(body);
      const policies = [
        ['enable_test_attendance', 'Online test attendance'],
        ['require_test_location', 'GPS workplace checks'],
        ['require_approved_device', 'Approved device required for online punches'],
        ['enable_offline_test', 'Offline test claims'],
        ['require_test_photo', 'Punch photo and GPS required'],
      ];
      const list = $('<ul></ul>').appendTo(body);
      policies.forEach(([key, label]) => $('<li></li>').text(`${__(label)}: ${data.policy[key] ? __('Enabled') : __('Disabled')}`).appendTo(list));
      if (data.can_edit_settings) button(body, 'Edit attendance settings', () => frappe.set_route('Form', 'Mobile Attendance Settings'));
      else $('<p class="text-muted"></p>').text(__('A System Manager can change this policy.')).appendTo(body);
      $('<h4 class="mt-4"></h4>').text(__('Review work')).appendTo(body);
      [
        ['pending_devices', 'Pending devices', 'Mobile Attendance Device', {status: 'Pending'}],
        ['approved_devices', 'Approved devices', 'Mobile Attendance Device', {status: 'Approved'}],
        ['pending_claims', 'Offline claims awaiting review', 'Mobile Attendance Offline Claim', {status: 'Pending Review'}],
        ['enabled_workplaces', 'Enabled workplaces', 'Mobile Attendance Workplace', {enabled: 1}],
      ].forEach(([key, label, doctype, filters]) => button(body, `${__(label)} (${data.counts[key]})`, () => route(doctype, filters)));
      button(body, 'Punch records and photos', () => route('Mobile Attendance Transaction'));
      button(body, 'All offline claims', () => route('Mobile Attendance Offline Claim'));
      button(body, 'Manage workplaces', () => route('Mobile Attendance Workplace'));
      $('<p class="text-muted mt-3"></p>').text(__('Open a device to approve or revoke it. Open an offline claim to inspect its photo and GPS, then approve or reject with a reason. Another HR reviewer must review their own device or claim. Counts follow your record access permissions.')).appendTo(body);
    } catch (_) {
      if (request !== generation) return;
      body.empty().append($('<p class="text-danger"></p>').text(__('Cannot load attendance controls. Check your permissions and connection, then refresh.')));
    }
  };
  page.set_secondary_action(__('Refresh'), wrapper.refresh_controls);
};
frappe.pages['maas-attendance-controls'].on_page_show = wrapper => wrapper.refresh_controls();

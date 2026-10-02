frappe.ui.form.on('Mobile Attendance Settings', {
  refresh(frm) {
    frm.add_custom_button(__('HR controls'), () => frappe.set_route('maas-attendance-controls'));
    frm.add_custom_button(__('Workplaces'), () => frappe.set_route('List', 'Mobile Attendance Workplace'));
    frm.add_custom_button(__('Company test policies'), () => frappe.set_route('List', 'Mobile Attendance Company Policy'));
    if (frappe.user_roles.includes('System Manager')) {
      frm.add_custom_button(__('OAuth clients'), () => frappe.set_route('List', 'OAuth Client'));
      frm.add_custom_button(__('Disable test submissions'), () => {
        frappe.confirm(__('Disable online attendance and offline uploads? Saved records remain available for review.'), async () => {
          await frm.set_value({enable_test_attendance: 0, enable_offline_test: 0});
          await frm.save();
        });
      });
    }
  }
});

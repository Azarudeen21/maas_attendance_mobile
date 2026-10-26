frappe.ui.form.on('Mobile Attendance Settings', {
  refresh(frm) {
    frm.add_custom_button(__('Mobile Attendance workspace'), () => frappe.set_route('mobile-attendance'));
    if (!frappe.user_roles.includes('System Manager') || !frm.doc.enable_mobile_attendance) return;
    frm.add_custom_button(__('Turn off mobile attendance'), () => {
      frappe.confirm(__('Stop all app check-ins and offline uploads now? Saved records stay available.'), async () => {
        await frm.set_value({enable_mobile_attendance: 0, enable_offline_sync: 0});
        await frm.save();
      });
    });
  },
});

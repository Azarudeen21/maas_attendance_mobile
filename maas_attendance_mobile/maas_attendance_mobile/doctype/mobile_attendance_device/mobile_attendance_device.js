frappe.ui.form.on('Mobile Attendance Device', {
  refresh(frm) {
    if (frm.is_new() || frm.doc.status === 'Revoked' || frm.doc.user === frappe.session.user ||
        !frappe.user_roles.some(role => ['HR Manager', 'System Manager'].includes(role))) return;
    const decisions = frm.doc.status === 'Pending' ? ['Approved', 'Revoked'] : ['Revoked'];
    decisions.forEach(status => frm.add_custom_button(status === 'Approved' ? __('Approve device') : __('Revoke device'), () => {
      frappe.confirm(status === 'Approved' ? __('Approve this installation for test attendance?') : __('Revoke this installation? It cannot be reactivated.'), async () => {
        await frm.set_value('status', status);
        await frm.save();
      });
    }));
  }
});

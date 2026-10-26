// Employee form: enroll the employee's saved photo for supervisor face check-in.
frappe.ui.form.on('Employee', {
  refresh(frm) {
    if (frm.is_new() || !frm.doc.image ||
        !frappe.user_roles.some(role => ['HR Manager', 'System Manager'].includes(role))) return;
    frm.add_custom_button(__('Create Face Enrollment'), () => {
      if (frm.is_dirty()) {
        frappe.msgprint(__('Save the employee first, so the current photo is used.'));
        return;
      }
      frappe.confirm(__("Is this employee's consent to face recognition on file? Their current photo will be enrolled."), async () => {
        try {
          const {message} = await frappe.call({
            method: 'maas_attendance_mobile.api.face_enroll_employee', type: 'POST',
            args: {employee: frm.doc.name, consent_confirmed: 1},
            freeze: true, freeze_message: __('Checking the photo...'),
          });
          const link = `<a href="/app/mobile-attendance-face-enrollment/${encodeURIComponent(message.enrollment)}">${frappe.utils.escape_html(message.enrollment)}</a>`;
          frappe.msgprint(message.outcome === 'enrolled'
            ? __('Face enrolled: {0}', [link])
            : __('This photo is already enrolled: {0}', [link]));
        } catch (_) { /* frappe.call already shows the server message. */ }
      });
    }, __('Face Recognition'));
  },
});

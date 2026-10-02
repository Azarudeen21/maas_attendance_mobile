// Shared by immutable receipt and offline-claim forms. Only the scoped API
// changes hold fields; ordinary saves cannot change evidence.
['Mobile Attendance Transaction', 'Mobile Attendance Offline Claim'].forEach(doctype => {
  frappe.ui.form.on(doctype, {
    refresh(frm) {
      if (frm.is_new() || !frm.doc.punch_photo || frm.doc.photo_removed_at ||
          !frappe.user_roles.some(role => ['HR Manager', 'System Manager'].includes(role))) return;
      const hold = frm.doc.photo_retention_hold ? 0 : 1;
      frm.add_custom_button(__(hold ? 'Keep photo on hold' : 'Release photo hold'), () => {
        frappe.prompt([{fieldname: 'reason', fieldtype: 'Small Text', label: __('Reason'), reqd: 1}], async values => {
          try {
            await frappe.call({method: 'maas_attendance_mobile.api.photo_retention_hold',
              type: 'POST', args: {doctype, name: frm.doc.name, hold, reason: values.reason}});
            await frm.reload_doc();
          } catch (_) { frappe.msgprint(__('Cannot change this photo hold. Check your access and try again.')); }
        }, __(hold ? 'Keep photo on hold' : 'Release photo hold'), __('Save'));
      });
    },
  });
});

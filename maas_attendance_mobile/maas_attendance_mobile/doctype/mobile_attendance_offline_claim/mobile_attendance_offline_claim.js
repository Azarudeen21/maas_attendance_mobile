frappe.ui.form.on('Mobile Attendance Offline Claim', {
  refresh(frm) {
    if (frm.doc.status !== 'Pending Review' || !(frappe.user_roles.includes('HR Manager') || frappe.user_roles.includes('System Manager'))) return;
    ['Approved', 'Rejected'].forEach(decision => {
      frm.add_custom_button(decision === 'Approved' ? __('Approve and create check-in') : __('Reject claim'), () => {
        frappe.prompt([{fieldname: 'reason', fieldtype: 'Small Text', label: __('Review reason'), reqd: 1}], values => {
          frappe.call({method: 'maas_attendance_mobile.api.review_offline_claim', args: {request_id: frm.doc.request_id, decision, reason: values.reason}, freeze: true})
            .then(() => frm.reload_doc());
        }, __('Review offline claim'), __('Submit review'));
      });
    });
  }
});

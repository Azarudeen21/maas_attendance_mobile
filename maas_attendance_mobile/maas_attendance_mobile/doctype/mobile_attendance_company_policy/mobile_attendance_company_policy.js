frappe.ui.form.on('Mobile Attendance Company Policy', {
  refresh(frm) {
    if (frm.is_new()) return;
    frm.add_custom_button(__('Preview photo retention'), async () => {
      try {
        const response = await frappe.call({method: 'maas_attendance_mobile.api.photo_retention_preview',
          type: 'GET', args: {company: frm.doc.company}});
        const data = response.message;
        if (data?.api_version !== 1 || data.company !== frm.doc.company) throw new Error('Invalid retention preview');
        frappe.msgprint({title: __('Photo retention preview'), message: [
          __('Eligible photos: {0}', [data.counts.eligible]),
          __('Photos on hold: {0}', [data.counts.held]),
          __('Awaiting HR review: {0}', [data.counts.awaiting_review]),
          __('Other retained photos: {0}', [data.counts.kept]),
          data.automatic_cleanup_enabled ? __('Automatic cleanup is enabled.') : __('Automatic cleanup is disabled.'),
        ].join('<br>')});
      } catch (_) {
        frappe.msgprint(__('Cannot preview photo retention. Check your permissions and connection.'));
      }
    });
  },
});

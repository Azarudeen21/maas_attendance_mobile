// HR pilot tools. Test photos are analysed in memory on the server and never stored.
frappe.listview_settings['Mobile Attendance Face Enrollment'] = {
  onload(listview) {
    if (!frappe.user_roles.some(role => ['HR Manager', 'System Manager'].includes(role))) return;
    listview.page.add_inner_button(__('Test face match'), () => maas_face_test_dialog());
    listview.page.add_inner_button(__('Enroll from Employee photos'), () => maas_face_bulk_dialog());
    frappe.realtime.off('maas_face_enrollment_done');
    frappe.realtime.on('maas_face_enrollment_done', summary => {
      const failed = summary.failed.map(row =>
        `<li>${frappe.utils.escape_html(row.employee)}: ${frappe.utils.escape_html(row.reason)}</li>`).join('');
      frappe.msgprint({
        title: __('Face enrollment finished'),
        message: __('{0} active employees with photos: {1} enrolled, {2} unchanged, {3} failed.',
          [summary.employees, summary.enrolled, summary.unchanged, summary.failed.length]) +
          (failed ? `<ul>${failed}</ul>` : ''),
      });
      listview.refresh();
    });
  },
};

// Downscale in the browser so phone photos upload quickly; orientation follows EXIF.
async function maas_face_prepare(file) {
  const bitmap = await createImageBitmap(file, {imageOrientation: 'from-image'});
  const scale = Math.min(1, 1280 / Math.max(bitmap.width, bitmap.height));
  const canvas = document.createElement('canvas');
  canvas.width = Math.round(bitmap.width * scale);
  canvas.height = Math.round(bitmap.height * scale);
  canvas.getContext('2d').drawImage(bitmap, 0, 0, canvas.width, canvas.height);
  const url = canvas.toDataURL('image/jpeg', 0.92);
  return {url, base64: url.split(',')[1]};
}

function maas_face_result_html(photo, result) {
  const labels = {
    match: __('Match'), ambiguous: __('Ambiguous: two employees score too closely'),
    no_match: __('No enrolled employee matched'), no_face: __('No clear face found'),
    face_too_small: __('Face too small'), multiple_faces: __('More than one face'),
    invalid_image: __('Invalid photo'),
  };
  const colour = result.decision === 'match' ? 'green' : result.decision === 'ambiguous' ? 'orange' : 'red';
  const rows = result.candidates.map(row => `<tr>
    <td><a href="/app/employee/${encodeURIComponent(row.employee)}">${frappe.utils.escape_html(row.employee_name || row.employee)}</a></td>
    <td>${frappe.utils.escape_html(row.employee)}</td><td>${row.score.toFixed(4)}</td></tr>`).join('');
  return `<div style="display:flex;gap:16px;flex-wrap:wrap;align-items:flex-start">
    <img src="${photo}" style="max-width:180px;max-height:180px;border-radius:8px">
    <div style="flex:1;min-width:220px">
      <p><span class="indicator-pill ${colour}">${frappe.utils.escape_html(labels[result.decision] || result.decision)}</span></p>
      ${result.message ? `<p>${frappe.utils.escape_html(result.message)}</p>` : ''}
      ${rows ? `<table class="table table-bordered table-sm"><thead><tr><th>${__('Employee')}</th><th>${__('ID')}</th>
        <th>${__('Score')}</th></tr></thead><tbody>${rows}</tbody></table>` : ''}
      <p class="text-muted small">${__('Threshold {0}, margin {1}, {2} employees compared. Detection score {3}.',
        [result.threshold, result.margin, result.compared ?? 0, result.face_score ?? '-'])}</p>
    </div></div>`;
}

function maas_face_test_dialog() {
  const dialog = new frappe.ui.Dialog({
    title: __('Test face match'),
    fields: [
      {fieldname: 'company', fieldtype: 'Link', options: 'Company', label: __('Company'),
        description: __('Leave empty to compare against every enrolled company you can read.')},
      {fieldname: 'picker', fieldtype: 'HTML'},
      {fieldname: 'result', fieldtype: 'HTML'},
    ],
    primary_action_label: __('Match'),
    async primary_action(values) {
      const file = dialog.fields_dict.picker.$wrapper.find('input')[0].files[0];
      if (!file) { frappe.msgprint(__('Choose a photo first.')); return; }
      let photo;
      try { photo = await maas_face_prepare(file); }
      catch (_) { frappe.msgprint(__('This browser cannot read that photo. Use a JPEG or PNG.')); return; }
      dialog.fields_dict.result.$wrapper.html(`<p class="text-muted">${__('Matching...')}</p>`);
      try {
        const response = await frappe.call({method: 'maas_attendance_mobile.api.face_test_match', type: 'POST',
          args: {image_base64: photo.base64, company: values.company || null}, freeze: true});
        dialog.fields_dict.result.$wrapper.html(maas_face_result_html(photo.url, response.message));
      } catch (_) { dialog.fields_dict.result.$wrapper.empty(); }
    },
  });
  dialog.fields_dict.picker.$wrapper.html(
    `<label class="control-label">${__('Photo')}</label><input type="file" accept="image/jpeg,image/png" class="form-control">`);
  dialog.show();
}

function maas_face_bulk_dialog() {
  frappe.prompt([
    {fieldname: 'company', fieldtype: 'Link', options: 'Company', label: __('Company'), reqd: 1},
    {fieldname: 'consent_confirmed', fieldtype: 'Check', label: __("Employees' consent to face recognition is on file"), reqd: 1},
  ], async values => {
    if (!values.consent_confirmed) { frappe.msgprint(__('Consent must be confirmed before enrollment.')); return; }
    await frappe.call({method: 'maas_attendance_mobile.api.face_enroll_employee_photos', type: 'POST',
      args: values, freeze: true});
    frappe.show_alert({message: __('Enrollment started. You will see a summary when it finishes.'), indicator: 'blue'});
  }, __('Enroll from Employee photos'), __('Start'));
}

"""Own-employee checkin history with time/name keyset pagination."""
import base64
import binascii
import json
from datetime import datetime

import frappe


def decode_cursor(value):
    if value is None or value == "":
        return None
    if not isinstance(value, str) or len(value) > 1000:
        raise ValueError("Invalid history cursor.")
    try:
        data = json.loads(base64.b64decode(value.encode(), altchars=b'-_', validate=True))
        if not isinstance(data, list) or len(data) != 2 or not isinstance(data[0], str) or not isinstance(data[1], str) or not 1 <= len(data[1]) <= 140:
            raise ValueError()
        at = datetime.fromisoformat(data[0])
        if at.tzinfo is not None:
            raise ValueError()
        return at, data[1]
    except (ValueError, TypeError, UnicodeError, binascii.Error):
        raise ValueError("Invalid history cursor.") from None


def encode_cursor(row):
    stamp = row['time']
    if isinstance(stamp, datetime):
        stamp = stamp.isoformat(timespec='microseconds')
    return base64.urlsafe_b64encode(json.dumps([stamp, row['name']], separators=(',', ':')).encode()).decode()


def page(employee, cursor=None):
    try:
        after = decode_cursor(cursor)
    except ValueError as error:
        frappe.throw(str(error), frappe.ValidationError)
    from frappe.query_builder import Order
    from frappe.utils import get_system_timezone
    table = frappe.qb.DocType('Employee Checkin')
    fields = [table.name, table.time, table.log_type]
    if frappe.get_meta('Employee Checkin').has_field('skip_auto_attendance'):
        fields.append(table.skip_auto_attendance)
    # Deliberate scoped self-service read: identity/Employee permissions were
    # checked by the API. No employee selector or free-form filter is accepted.
    query = frappe.qb.from_(table).select(*fields).where(table.employee == employee['name'])
    if after:
        at, name = after
        query = query.where((table.time < at) | ((table.time == at) & (table.name < name)))
    rows = query.orderby(table.time, table.name, order=Order.desc).limit(21).run(as_dict=True)
    selected = rows[:20]
    return {'api_version': 1, 'user': frappe.session.user, 'timezone': get_system_timezone(),
        'records': [{'name': row['name'], 'time': row['time'], 'log_type': row['log_type'],
                     'skip_auto_attendance': bool(row['skip_auto_attendance']) if 'skip_auto_attendance' in row else None} for row in selected],
        'next_cursor': encode_cursor(selected[-1]) if len(rows) > 20 else None}

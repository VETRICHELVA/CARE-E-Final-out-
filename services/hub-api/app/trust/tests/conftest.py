"""S19 fixtures: Scenario 1 up to a DELIVERED transfer or purchase (S12's fixtures)."""

from app.receiving.tests import conftest as s12

now = s12.now
s1 = s12.s1
shortage = s12.shortage
swiftmed = s12.swiftmed
other_fleet = s12.other_fleet
shipment = s12.shipment
dispatcher = s12.dispatcher
ravi = s12.ravi
delivered = s12.delivered
desk_y = s12.desk_y
po_delivered = s12.po_delivered
receiver = s12.receiver

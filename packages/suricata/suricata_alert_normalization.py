# Calculation of type_uid
self.type_uid = self.class_uid * 100 + self.activity_id

# Calculation of severity_id.
# Atm this is kind of ambigouos at the moment since ocsf has 8 different possible values and suricata has 3.
severity_id_mapping = {
    6: "1",  # Fatal
    3: "2",  # Medium
    1: "3",  # Informational
}
self.severity_id = severity_id_mapping.get(self.severity_id, "99")

# Set severity
severity_mapping = {
    "0": "Unknown",
    "1": "Informational",
    "2": "Low",
    "3": "Medium",
    "4": "High",
    "5": "Critical",
    "6": "Fatal",
    "99": "Other",
}
self.severity = severity_mapping.get(self.severity_id)


# Calculation of status_id
# Todo: The given suricata.alert's dont really have a field that would indicate a status
# My best guess is to set the status_id of each alert to 1 (new finding)
self.status_id = 1

# Set status_name
status_mapping = {
    0: "Unknown",
    1: "New",
    2: "In Progress",
    3: "Suppressed",
    4: "Resolved",
    99: "Other",
}
self.status = status_mapping.get(self.status_id, "Unknown")

# Set type_name
type_mapping = {
    200400: "Detection Finding: Unknown",
    200401: "Detection Finding: A finding was created.",
    200402: "Detection Finding: A finding was updated.",
    200403: "Detection Finding: A finding was closed.",
    200499: "Detection Finding: Other.",
}
self.type_name = type_mapping.get(self.type_uid)


# Set confidence
confidence_mapping = {
    0: "Unknown",
    1: "Low",
    2: "Medium",
    3: "High",
    99: "Other",
}
self.confidence = confidence_mapping.get(self.confidence_id)


# Set class_name
class_mapping = {
    2004: "Detection Finding",
}
self.class_name = class_mapping.get(self.class_uid, "Other")

# Set category_name
category_mapping = {
    2: "Findings",
}
self.category_name = category_mapping.get(self.category_uid, "Other")

# Skeleton for General Findings

# Set class_name
class_mapping = {
    2002: "Vulnerability Finding",
    2003: "Compliance Finding",
    2004: "Detection Finding",
    2005: "Incident Finding",
    2006: "Data Security Finding",
}
self.class_name = class_mapping.get(self.class_uid)

# Calculation of type_uid
# activity_id has to be set before (via tenzir)
self.type_uid = self.class_uid * 100 + self.activity_id

# Set type_name
type_mapping = {
    # Vulnerability
    200200: "Vulnerability Finding: Unknown",
    200201: "Vulnerability Finding: A finding was created.",
    200202: "Vulnerability Finding: A finding was updated.",
    200203: "Vulnerability Finding: A finding was closed.",
    200299: "Vulnerability Finding: Other.",
    # Compliance
    200300: "Compliance Finding: Unknown",
    200301: "Compliance Finding: A finding was created.",
    200302: "Compliance Finding: A finding was updated.",
    200303: "Com99pliance Finding: A finding was closed.",
    200399: "Compliance Finding: Other.",
    # Detection
    200400: "Detection Finding: Unknown",
    200401: "Detection Finding: A finding was created.",
    200402: "Detection Finding: A finding was updated.",
    200403: "Detection Finding: A finding was closed.",
    200499: "Detection Finding: Other.",
    # Incident
    200500: "Incident Finding: Unknown",
    200501: "Incident Finding: A finding was created.",
    200502: "Incident Finding: A finding was updated.",
    200503: "Incident Finding: A finding was closed.",
    200599: "Incident Finding: Other.",
    # Data Security
    200600: "Data Security Finding: Unknown",
    200601: "Data Security Finding: A finding was created.",
    20062: "Data Security Finding: A finding was updated.",
    200603: "Data Security Finding: A finding was closed.",
    200699: "Data Security Finding: Other.",
}
self.type_name = type_mapping.get(self.type_uid)

# Set category_name
category_mapping = {
    1: "System Activity",
    2: "Findings",
    3: "Identity & Access Management",
    4: "Network Activity",
    5: "Discovery",
    6: "Application Activity",
}
self.category_name = category_mapping.get(self.category_uid)

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

# Set activity_name
activity_mapping = {
    200100: "Unknown",
    200101: "Create",
    200102: "Update",
    200103: "Close",
    200199: "Other",
}
self.activity_name = activity_mapping.get(self.activity_id)

# Set status_name
status_mapping = {
    0: "Unknown",
    1: "New",
    2: "In Progress",
    3: "Suppressed",
    4: "Resolved",
    99: "Other",
}
self.status = status_mapping.get(self.status_id)

# Set confidence
confidence_mapping = {
    0: "Unknown",
    1: "Low",
    2: "Medium",
    3: "High",
    99: "Other",
}
self.confidence = confidence_mapping.get(self.confidence_id)

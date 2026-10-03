"""Deterministic fixture data for the two seeded schools."""
from __future__ import annotations

from dataclasses import dataclass, field

MASTER_PW = "Master@12345"
ADMIN_PW = "Admin@12345"
TEACHER_PW = "Teacher@12345"
STAFF_PW = "Staff@12345"
PARENT_PW = "Parent@12345"
PILOT_PW = "Pilot@12345"

SUBJECTS = [
    "Mathematics", "English", "Science", "Hindi",
    "Social Studies", "Computer Science", "Physical Education", "Art",
]

# 8 teaching periods + 1 break. Each POST creates a global period row; the
# seeder collapses duplicates back to exactly these nine (see seed.normalize).
PERIOD_TIMES = [
    "08:00 AM - 08:45 AM",
    "08:45 AM - 09:30 AM",
    "09:30 AM - 10:15 AM",
    "10:15 AM - 11:00 AM",
    "11:00 AM - 11:30 AM",   # break
    "11:30 AM - 12:15 PM",
    "12:15 PM - 01:00 PM",
    "01:00 PM - 01:45 PM",
    "01:45 PM - 02:30 PM",
]
BREAK_INDEX = 4

DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri"]


def _username(code: str, index: int) -> str:
    return f"{code}{index}"


@dataclass
class SchoolCfg:
    code: str                 # short prefix used for usernames, e.g. "bh"
    name: str
    city: str
    state: str
    pincode: str
    address: str
    primary_contact: str
    primary_email: str
    master_username: str
    first_name: str
    last_name: str
    teacher_names: list[str]
    staff_names: list[str]
    parent_names: list[str]
    student_names: list[str]
    vehicle_numbers: list[str]
    route_names: list[str]
    pilot_names: list[str]
    clubs: list[str] = field(default_factory=list)


SCHOOLS = [
    SchoolCfg(
        code="bh",
        name="Blue Horizon Academy",
        city="Bengaluru",
        state="Karnataka",
        pincode="560001",
        address="12 MG Road, Bengaluru",
        primary_contact="8105096987",
        primary_email="ravigupta0307@gmail.com",
        master_username="meera.nair",
        first_name="Blue",
        last_name="Horizon",
        teacher_names=[
            "Aarav Sharma", "Priya Nair", "Rohan Iyer", "Sneha Reddy",
            "Vikram Singh", "Ananya Rao", "Karthik Menon", "Divya Pillai",
        ],
        staff_names=["Suresh Kumar", "Latha Krishnan", "Mahesh Patil", "Rekha Verma"],
        parent_names=[
            "Naveen Gupta", "Pooja Mehta", "Arjun Desai", "Kavya Krishnan",
            "Sanjay Verma", "Ritu Agarwal", "Deepak Joshi", "Meena Iyer",
            "Rahul Bose", "Suman Das", "Farhan Khan", "Neha Kapoor",
        ],
        student_names=[
            "Aadhya Rao", "Aryan Nair", "Diya Menon", "Ishaan Gupta", "Saanvi Iyer",
            "Vivaan Shah", "Anaya Reddy", "Reyansh Patel", "Myra Singh",
            "Kabir Das", "Riya Bose", "Advik Jain", "Kiara Kapoor", "Dhruv Verma",
            "Aarohi Gupta", "Arnav Mehta", "Pari Joshi", "Vihaan Nair",
        ],
        vehicle_numbers=["KA-01-AB-1234", "KA-01-CD-5678", "KA-01-EF-9012"],
        route_names=["Indiranagar Route", "Whitefield Route", "Jayanagar Route"],
        pilot_names=["Ganesh Pawar", "Ravi Yadav", "Suresh Mudaliar"],
        clubs=["Robotics Club", "Music Club", "Debate Society", "Eco Club"],
    ),
    SchoolCfg(
        code="gv",
        name="Green Valley Public School",
        city="Pune",
        state="Maharashtra",
        pincode="411001",
        address="45 FC Road, Shivajinagar, Pune",
        primary_contact="8105096987",
        primary_email="ravigupta0307@gmail.com",
        master_username="ravi",
        first_name="Green",
        last_name="Valley",
        teacher_names=[
            "Aditya Kulkarni", "Shreya Deshpande", "Nikhil Joshi", "Tanvi Gokhale",
            "Sameer Kale", "Isha Bapat", "Omkar Shinde", "Pallavi More",
        ],
        staff_names=["Ramesh Gaikwad", "Sunita Kadam", "Vijay Wagh", "Asha Salunkhe"],
        parent_names=[
            "Anil Pawar", "Snehal Patil", "Rahul Chavan", "Madhuri Jagtap",
            "Sachin Bhosale", "Varsha Naik", "Prakash Sawant", "Usha Ghadge",
            "Nitin Dubey", "Swati Kale", "Imran Shaikh", "Priti Rane",
        ],
        student_names=[
            "Ira Kulkarni", "Arjun Deshpande", "Sara Joshi", "Neel Gokhale",
            "Anvi Kale", "Aditya Bapat", "Zoya Shinde", "Rudra More",
            "Aarohi Pawar", "Shivansh Patil", "Naisha Chavan", "Shaurya Jagtap",
            "Ahana Bhosale", "Yash Naik", "Mahi Sawant", "Rachit Ghadge",
            "Sanvi Dubey", "Kian Shaikh",
        ],
        vehicle_numbers=["MH-12-AB-3456", "MH-12-CD-7890", "MH-12-EF-2345"],
        route_names=["Baner Route", "Kothrud Route", "Hadapsar Route"],
        pilot_names=["Ramesh Jadhav", "Sandip More", "Ganesh Bhosale"],
        clubs=["Chess Club", "Drama Club", "Science Club", "Sports Club"],
    ),
]

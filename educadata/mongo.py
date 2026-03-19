from pymongo import MongoClient

from .settings import MONGODB_SETTINGS


def get_mongo_client():
    return MongoClient(MONGODB_SETTINGS["uri"])


def get_mongo_database():
    return get_mongo_client()[MONGODB_SETTINGS["database"]]

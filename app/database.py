from pymongo import MongoClient
from pymongo.collection import Collection
from pymongo.errors import ConnectionFailure
from .settings import settings


class Database:
    def __init__(self):
        self.client = MongoClient(settings.MONGODB_URI)
        print("Attempting to connect to MongoDB")
        try:
            self.client.admin.command("ping")
            print("Successfully connected to MongoDB")
        except ConnectionFailure:
            raise ConnectionError("Failed to connect to MongoDB")
        self.db = self.client["bg_removal_db"]
        self.images_collection: Collection = self.db["vendor_images"]
        self.folders_collection: Collection = self.db["vendor_folders"]

        # Create index on vendor_id for faster queries
        self.images_collection.create_index("vendor_id")


db = Database()

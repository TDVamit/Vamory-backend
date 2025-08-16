# vector_db.py

import os
import faiss
import pickle
from typing import List, Dict
from uuid import UUID
from langchain_community.vectorstores import FAISS
from langchain_community.docstore.in_memory import InMemoryDocstore
from langchain_openai import OpenAIEmbeddings
from langchain_core.documents import Document

from app.config import settings


class VectorDB:
    def __init__(self, store_path: str = None):
        if store_path is None:
            # Use absolute path in the project root
            current_dir = os.path.dirname(os.path.abspath(__file__))
            project_root = os.path.dirname(os.path.dirname(current_dir))
            self.store_path = os.path.join(project_root, "my_vectordb")
        else:
            self.store_path = store_path
            
        print(f"🔍 Vector DB store path: {self.store_path}")
        
        self.embeddings = OpenAIEmbeddings(model=settings.OPENAI_EMBEDDING_MODEL, openai_api_key=settings.OPENAI_API_KEY)
        self.index = None
        self.docstore = InMemoryDocstore()
        self.index_to_id = {}
         
        if os.path.exists(self.store_path):
            print(f"📂 Loading existing vector DB from: {self.store_path}")
            self._load()
        else:
            print(f"🆕 Creating new vector DB at: {self.store_path}")
            # Create directory if it doesn't exist
            os.makedirs(os.path.dirname(self.store_path), exist_ok=True)
            
            dim = len(self.embeddings.embed_query("hello"))
            raw_index = faiss.IndexFlatL2(dim)
            self.vectorstore = FAISS(
                embedding_function=self.embeddings,
                index=raw_index,
                docstore=self.docstore,
                index_to_docstore_id=self.index_to_id
            )

    def add(self, desc: str, image_id: str, owner_id: str):
        try:
            metadata = {"image_id": image_id, "owner_id": owner_id}
            doc = Document(page_content=desc, metadata=metadata)
            
            # Use the image_id directly as the document ID for FAISS
            # This ensures consistent mapping between FAISS index and docstore
            self.vectorstore.add_documents(documents=[doc], ids=[image_id])
            print(f"✅ Added to vector DB: {image_id}")
            
            # Save immediately to ensure persistence
            self._save()
            
            # Force a complete rebuild of the vectorstore to ensure consistency
            print(f"🔄 Rebuilding vector DB for consistency...")
            self._rebuild_vectorstore()
            
            # Verify the document was added by checking the current count
            current_count = len(self.vectorstore.index_to_docstore_id)
            print(f"📊 Vector DB now contains {current_count} documents")
            
            # Double-check that the document is actually in the vectorstore
            if image_id in self.vectorstore.index_to_docstore_id:
                print(f"✅ Confirmed: {image_id} is in vectorstore mapping after rebuild")
            else:
                print(f"⚠️  Warning: {image_id} still not found in vectorstore mapping after rebuild")
            
        except Exception as e:
            print(f"❌ Error adding to vector DB: {str(e)}")
            import traceback
            traceback.print_exc()
            raise

    def query(self, desc: str, owner_id: str, top_k: int = 5) -> List[Dict]:
        try:
            query_str = desc
            
            # Debug: Check current state of vectorstore
            total_docs = len(self.vectorstore.index_to_docstore_id)
            print(f"🔍 Querying vector DB: '{query_str}' for owner: {owner_id}")
            print(f"📊 Total documents in vector DB: {total_docs}")
            
            # Debug: Show all document IDs in the vectorstore
            doc_ids = list(self.vectorstore.index_to_docstore_id.keys())
            print(f"📋 Document IDs in vectorstore: {doc_ids}")
            
            results = self.vectorstore.similarity_search(query=query_str, k=top_k, filter={"owner_id": owner_id})
            
            print(f"✅ Found {len(results)} results")
            
            # Debug: Show the image IDs from results
            result_image_ids = [doc.metadata["image_id"] for doc in results]
            print(f"🎯 Result image IDs: {result_image_ids}")
            
            formatted_results = [
                {"desc": doc.page_content, "image_id": doc.metadata["image_id"]}
                for doc in results
            ]
            return formatted_results
            
        except Exception as e:
            print(f"❌ Error in vector DB query: {str(e)}")
            import traceback
            traceback.print_exc()
            return []

    def delete(self, doc_id: str):
        """Delete a document from the vector database by ID"""
        try:
            # Extract image_id from the doc_id format "owner_id:image_id"
            if ":" in doc_id:
                image_id = doc_id.split(":", 1)[1]
            else:
                image_id = doc_id
                
            # Remove from the docstore
            if image_id in self.vectorstore.index_to_docstore_id:
                # Get the internal index
                internal_id = self.vectorstore.index_to_docstore_id[image_id]
                
                # Remove from docstore
                if internal_id in self.vectorstore.docstore._dict:
                    del self.vectorstore.docstore._dict[internal_id]
                
                # Remove from index_to_docstore_id mapping
                del self.vectorstore.index_to_docstore_id[image_id]
                
                # Save the updated vectorstore
                self._save()
                print(f"🗑️  Deleted vector DB document: {image_id}")
            else:
                print(f"⚠️  Document {image_id} not found in vector DB")
        except Exception as e:
            print(f"⚠️  Error deleting from vector DB: {str(e)}")

    def get_status(self) -> Dict:
        """Get the status of the vector database"""
        try:
            total_docs = len(self.vectorstore.index_to_docstore_id)
            store_exists = os.path.exists(self.store_path)
            store_size = os.path.getsize(self.store_path) if store_exists else 0
            
            return {
                "total_documents": total_docs,
                "store_path": self.store_path,
                "store_exists": store_exists,
                "store_size_bytes": store_size,
                "doc_ids": list(self.vectorstore.index_to_docstore_id.keys()),
                "docstore_docs": len(self.vectorstore.docstore._dict) if hasattr(self.vectorstore, 'docstore') else 0
            }
        except Exception as e:
            return {
                "error": str(e),
                "store_path": self.store_path,
                "store_exists": os.path.exists(self.store_path) if hasattr(self, 'store_path') else False
            }
    


    def _save(self):
        try:
            print(f"💾 Saving vector DB to: {self.store_path}")
            self.vectorstore.save_local(self.store_path)
            print(f"✅ Vector DB saved successfully")
        except Exception as e:
            print(f"❌ Error saving vector DB: {str(e)}")
            import traceback
            traceback.print_exc()
            raise

    def clear_and_reset(self):
        """Clear the vector database and create a fresh one"""
        try:
            print(f"🗑️  Clearing vector DB at: {self.store_path}")
            
            # Remove the existing store directory
            if os.path.exists(self.store_path):
                import shutil
                shutil.rmtree(self.store_path)
                print(f"✅ Removed existing vector DB directory")
            
            # Create a fresh vector database
            print(f"🆕 Creating fresh vector DB")
            dim = len(self.embeddings.embed_query("hello"))
            raw_index = faiss.IndexFlatL2(dim)
            self.vectorstore = FAISS(
                embedding_function=self.embeddings,
                index=raw_index,
                docstore=self.docstore,
                index_to_docstore_id=self.index_to_id
            )
            
            # Save the fresh database
            self._save()
            print(f"✅ Fresh vector DB created and saved")
            return True
            
        except Exception as e:
            print(f"❌ Error clearing vector DB: {str(e)}")
            import traceback
            traceback.print_exc()
            return False

    def _load(self):
        try:
            print(f"📂 Loading vector DB from: {self.store_path}")
            self.vectorstore = FAISS.load_local(self.store_path, self.embeddings, allow_dangerous_deserialization=True)
            print(f"✅ Vector DB loaded successfully")
            
        except Exception as e:
            print(f"❌ Error loading vector DB: {str(e)}")
            import traceback
            traceback.print_exc()
            # If loading fails, create a new one
            print(f"🆕 Creating new vector DB due to loading error")
            dim = len(self.embeddings.embed_query("hello"))
            raw_index = faiss.IndexFlatL2(dim)
            self.vectorstore = FAISS(
                embedding_function=self.embeddings,
                index=raw_index,
                docstore=self.docstore,
                index_to_docstore_id=self.index_to_id
            )

    def _reload_vectorstore(self):
        """Reload the vectorstore from disk to ensure it's up to date"""
        try:
            if os.path.exists(self.store_path):
                print(f"🔄 Reloading vector DB from: {self.store_path}")
                # Store the current count before reload
                old_count = len(self.vectorstore.index_to_docstore_id) if hasattr(self, 'vectorstore') else 0
                
                # Reload from disk
                self.vectorstore = FAISS.load_local(self.store_path, self.embeddings, allow_dangerous_deserialization=True)
                
                # Check the new count
                new_count = len(self.vectorstore.index_to_docstore_id)
                print(f"✅ Vector DB reloaded successfully: {old_count} → {new_count} documents")
                
                if new_count < old_count:
                    print(f"⚠️  Warning: Document count decreased after reload ({old_count} → {new_count})")
                    
        except Exception as e:
            print(f"⚠️  Error reloading vector DB: {str(e)}")
            # Don't raise the error, just log it
            # The vectorstore should still work with the in-memory version

    def force_reload(self):
        """Force reload the vector database from disk"""
        try:
            print(f"🔄 Force reloading vector DB")
            self._reload_vectorstore()
            return True
        except Exception as e:
            print(f"❌ Error force reloading vector DB: {str(e)}")
            return False

    def force_rebuild(self):
        """Force rebuild the vector database from disk"""
        try:
            print(f"🔨 Force rebuilding vector DB")
            self._rebuild_vectorstore()
            return True
        except Exception as e:
            print(f"❌ Error force rebuilding vector DB: {str(e)}")
            return False

    def _rebuild_vectorstore(self):
        """Completely rebuild the vectorstore from disk to ensure consistency"""
        try:
            print(f"🔨 Rebuilding vector DB from disk...")
            
            # Save current state to disk first
            self._save()
            
            # Load from disk to get a clean state
            if os.path.exists(self.store_path):
                # Load the saved vectorstore
                loaded_vectorstore = FAISS.load_local(self.store_path, self.embeddings, allow_dangerous_deserialization=True)
                
                # Extract all documents and their IDs from the loaded vectorstore
                all_docs = []
                all_ids = []
                
                for doc_id, internal_id in loaded_vectorstore.index_to_docstore_id.items():
                    if internal_id in loaded_vectorstore.docstore._dict:
                        doc = loaded_vectorstore.docstore._dict[internal_id]
                        all_docs.append(doc)
                        all_ids.append(doc_id)
                
                print(f"📋 Found {len(all_docs)} documents to rebuild")
                
                # Create a completely fresh vectorstore
                dim = len(self.embeddings.embed_query("hello"))
                raw_index = faiss.IndexFlatL2(dim)
                
                # Create fresh docstore and mapping
                fresh_docstore = InMemoryDocstore()
                fresh_index_to_id = {}
                
                # Rebuild the vectorstore with all documents
                if all_docs:
                    fresh_vectorstore = FAISS(
                        embedding_function=self.embeddings,
                        index=raw_index,
                        docstore=fresh_docstore,
                        index_to_docstore_id=fresh_index_to_id
                    )
                    
                    # Add all documents back with their original IDs
                    fresh_vectorstore.add_documents(documents=all_docs, ids=all_ids)
                    
                    # Replace the current vectorstore
                    self.vectorstore = fresh_vectorstore
                    
                    # Save the rebuilt vectorstore
                    self._save()
                    
                    print(f"✅ Rebuilt vector DB with {len(all_docs)} documents")
                else:
                    print(f"⚠️  No documents found to rebuild")
            else:
                print(f"⚠️  No saved vector DB found to rebuild from")
                
        except Exception as e:
            print(f"❌ Error rebuilding vector DB: {str(e)}")
            import traceback
            traceback.print_exc()
            # Fallback to reload
            self._reload_vectorstore()

"""
归档服务测试
"""
import pytest
from app.config import settings
from app.services.archive_service import ArchiveService
from app.services.firestore_service import FirestoreService
from app.models import MessageCreate, MessageRole


class TestArchiveService:
    """归档服务测试"""
    
    @pytest.mark.asyncio
    async def test_archive_execution(self):
        """测试：归档执行流程"""
        firestore = FirestoreService()
        archive_service = ArchiveService()
        user_id = "test_user_archive_001"
        
        session = await firestore.create_session(user_id)
        session_id = session.session_id
        
        original_threshold = settings.archive_threshold
        original_window = settings.active_window_size
        settings.archive_threshold = 3
        settings.active_window_size = 1
        
        try:
            for i in range(3):
                role = MessageRole.USER if i % 2 == 0 else MessageRole.ASSISTANT
                msg = MessageCreate(
                    role=role,
                    content=f"归档测试消息 {i}",
                    is_archived=False,
                )
                await firestore.add_message(user_id, session_id, msg)
            
            should_archive = await archive_service.check_should_archive(
                user_id, session_id
            )
            assert should_archive is True
            
            thread_id = await archive_service.execute_archive(user_id, session_id)
            assert thread_id is not None
            
            messages = await firestore.get_messages_by_session(user_id, session_id)
            archived_count = sum(1 for msg in messages if msg.is_archived)
            assert archived_count >= 2
        finally:
            settings.archive_threshold = original_threshold
            settings.active_window_size = original_window

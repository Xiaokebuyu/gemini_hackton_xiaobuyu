"""
Artifact 管理测试
"""
import pytest
from app.services.artifact_service import ArtifactService


class TestArtifactService:
    """Artifact 服务测试"""
    
    @pytest.fixture
    def artifact_service(self):
        """创建 Artifact 服务实例"""
        return ArtifactService()
    
    def test_parse_artifact_sources(self, artifact_service):
        """测试：解析 Artifact 源索引"""
        artifact = """# Python学习笔记

## 列表推导式 <!-- sources: msg_001, msg_002 -->
基本语法是 `[expr for x in iterable if cond]`

## 异步编程 <!-- sources: msg_015, msg_016 -->
使用 asyncio 处理 IO 密集型任务

### 具体实现 <!-- sources: msg_017 -->
使用 `asyncio.gather()` 并发执行
"""
        
        sources_map = artifact_service.parse_artifact_sources(artifact)
        
        # 验证解析结果
        assert "## 列表推导式" in sources_map
        assert sources_map["## 列表推导式"] == ["msg_001", "msg_002"]
        
        assert "## 异步编程" in sources_map
        assert sources_map["## 异步编程"] == ["msg_015", "msg_016"]
        
        assert "### 具体实现" in sources_map
        assert sources_map["### 具体实现"] == ["msg_017"]
    
    def test_parse_artifact_sources_no_sources(self, artifact_service):
        """测试：解析没有源索引的 Artifact"""
        artifact = """# Python学习笔记

## 列表推导式
基本语法

## 异步编程
使用 asyncio
"""
        
        sources_map = artifact_service.parse_artifact_sources(artifact)
        
        # 应该返回空字典
        assert sources_map == {}
    
    def test_extract_section_content(self, artifact_service):
        """测试：提取章节内容"""
        artifact = """# Python学习笔记

## 列表推导式
基本语法是 `[expr for x in iterable]`
可以带条件过滤

## 异步编程
使用 asyncio 处理任务

### 具体实现
使用 gather 并发执行
"""
        
        # 提取二级标题
        section = artifact_service.extract_section_content(
            artifact,
            "## 列表推导式"
        )
        
        assert section is not None
        assert "基本语法" in section
        assert "可以带条件过滤" in section
        assert "异步编程" not in section  # 不应包含下一个章节
        
        # 提取三级标题
        subsection = artifact_service.extract_section_content(
            artifact,
            "### 具体实现"
        )
        
        assert subsection is not None
        assert "gather 并发执行" in subsection
    
    def test_extract_nonexistent_section(self, artifact_service):
        """测试：提取不存在的章节"""
        artifact = """# Python学习笔记

## 列表推导式
基本语法
"""
        
        section = artifact_service.extract_section_content(
            artifact,
            "## 不存在的章节"
        )
        
        assert section is None


class TestArtifactParsing:
    """Artifact 解析相关测试"""
    
    def test_markdown_structure_parsing(self):
        """测试：Markdown 结构解析"""
        artifact = """# 主标题

## 二级标题1
内容1

### 三级标题1.1
子内容1.1

## 二级标题2 <!-- sources: msg_001 -->
内容2
"""
        
        service = ArtifactService()
        sources = service.parse_artifact_sources(artifact)
        
        # 只有带 sources 注释的应该被解析
        assert len(sources) == 1
        assert "## 二级标题2" in sources


def run_tests():
    """运行测试"""
    print("运行 Artifact 管理测试...")
    print("\n建议使用 pytest 命令运行测试：")
    print("  cd backend")
    print("  pytest tests/test_artifact.py -v")


if __name__ == "__main__":
    run_tests()

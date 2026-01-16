"""
Artifact 管理服务
"""
import re
from typing import List, Dict, Any, Optional, Tuple


class ArtifactService:
    """Artifact 管理服务类"""
    
    def parse_artifact_sources(self, artifact: str) -> Dict[str, List[str]]:
        """
        解析 Artifact 中的源消息索引
        
        Args:
            artifact: Artifact 内容
            
        Returns:
            Dict[str, List[str]]: {章节标题: [消息ID列表]}
        """
        sources_map: Dict[str, List[str]] = {}
        pattern = r'(#{1,6}\s+[^\n]+?)\s*<!--\s*sources:\s*([^>]+?)\s*-->'
        
        for match in re.finditer(pattern, artifact):
            title = match.group(1).strip()
            sources_str = match.group(2).strip()
            message_ids = [
                msg_id.strip()
                for msg_id in sources_str.split(',')
                if msg_id.strip()
            ]
            if message_ids:
                sources_map[title] = message_ids
        
        return sources_map
    
    def extract_section_content(self, artifact: str, section_title: str) -> Optional[str]:
        """
        提取指定章节的内容
        
        Args:
            artifact: Artifact 完整内容
            section_title: 章节标题（如 "## 列表推导式"）
            
        Returns:
            Optional[str]: 章节内容，如果不存在返回 None
        """
        if not artifact or not section_title:
            return None
        
        lines = artifact.split('\n')
        title_match = re.match(r'^(#{1,6})\s+(.+)', section_title.strip())
        if not title_match:
            return None
        
        target_level = len(title_match.group(1))
        target_title = title_match.group(2).strip()
        start_idx = None
        
        for i, line in enumerate(lines):
            line_match = re.match(r'^(#{1,6})\s+(.+)', line.strip())
            if line_match:
                level = len(line_match.group(1))
                title = line_match.group(2).strip()
                title = re.sub(r'\s*<!--.*?-->\s*$', '', title).strip()
                if level == target_level and title == target_title:
                    start_idx = i
                    break
        
        if start_idx is None:
            return None
        
        end_idx = len(lines)
        for i in range(start_idx + 1, len(lines)):
            line_match = re.match(r'^(#{1,6})\s+', lines[i].strip())
            if line_match:
                level = len(line_match.group(1))
                if level <= target_level:
                    end_idx = i
                    break
        
        section_lines = lines[start_idx:end_idx]
        return '\n'.join(section_lines)
    
    def get_all_sections(self, artifact: str) -> List[Dict[str, Any]]:
        """
        获取 Artifact 中所有 section
        
        Returns:
            [
                {"title": "## 列表推导式", "content": "...", "sources": ["msg_001"]},
                ...
            ]
        """
        if not artifact:
            return []
        
        sections: List[Dict[str, Any]] = []
        current_section: Optional[Dict[str, Any]] = None
        
        for line in artifact.splitlines():
            header_match = re.match(r'^(#{1,6})\s+(.+)', line.strip())
            if header_match:
                level = len(header_match.group(1))
                if level == 1:
                    if current_section:
                        current_section["content"] = "\n".join(current_section["content"]).strip()
                        sections.append(current_section)
                        current_section = None
                    continue
                if current_section:
                    current_section["content"] = "\n".join(current_section["content"]).strip()
                    sections.append(current_section)
                title = header_match.group(0).strip()
                title = re.sub(r'\s*<!--.*?-->\s*$', '', title).strip()
                current_section = {"title": title, "content": []}
                continue
            
            if current_section is not None:
                current_section["content"].append(line)
        
        if current_section:
            current_section["content"] = "\n".join(current_section["content"]).strip()
            sections.append(current_section)
        
        sources_map = self.parse_artifact_sources(artifact)
        for section in sections:
            section["sources"] = sources_map.get(section["title"], [])
        
        return sections
    
    def find_section_by_keyword(
        self, artifact: str, keyword: str
    ) -> Optional[Tuple[str, List[str]]]:
        """
        根据关键词查找匹配的 section
        
        返回: (section_title, message_ids) 或 None
        """
        if not artifact or not keyword:
            return None
        
        needle = keyword.lower()
        sections = self.get_all_sections(artifact)
        
        for section in sections:
            title = section.get("title", "")
            content = section.get("content", "")
            if needle in title.lower() or needle in content.lower():
                message_ids = section.get("sources", [])
                if message_ids:
                    return title, message_ids
        
        return None

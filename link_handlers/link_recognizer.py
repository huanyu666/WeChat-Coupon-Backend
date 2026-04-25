"""
链接识别器

"""
import re
from typing import Optional, Dict, Any, List


class LinkRecognizer:
    
    def __init__(self, config: Dict[str, Any]):
        """
        初始化链接识别器
        
        Args:
            config: 识别配置字典，包含各种链接的识别模式
        """
        self.config = config
    
    def recognize(self, text: str) -> Optional[Dict[str, str]]:
        """
        识别文本中的单个链接类型
        
        Args:
            text: 用户输入的文本
            
        Returns:
            如果未识别到，返回None
        """
        for link_type, pattern_config in self.config.items():
                                        
            patterns = pattern_config.get("patterns", [])
            if not patterns:
                                 
                pattern = pattern_config.get("pattern", "")
                if pattern:
                    patterns = [pattern]
            
                        
            for pattern in patterns:
                if pattern in text:
                    result = {
                        "type": link_type,
                        "content": text.strip(),
                        "matched_pattern": pattern
                    }
                    
                    
                    return result
        
        return None
    
    def recognize_multiple(self, text: str) -> List[Dict[str, str]]:
        """
        识别文本中的多个链接
        
        支持两种格式：
        1. 空格分割: "#小程序://xxx #小程序://yyy"
        2. 连续链接: "#小程序://xxx#小程序://yyy"
        
        Args:
            text: 用户输入的文本
            
        Returns:
            识别结果列表，每个元素是一个链接字典
        """
        results = []
        
                       
        all_patterns = []
        for link_type, pattern_config in self.config.items():
            patterns = pattern_config.get("patterns", [])
            if not patterns:
                pattern = pattern_config.get("pattern", "")
                if pattern:
                    patterns = [pattern]
            all_patterns.extend(patterns)
        
        if not all_patterns:
            return results
        
                        
                        
                                     
        escaped_patterns = [re.escape(p) for p in all_patterns]
        pattern_regex = '|'.join(escaped_patterns)
        
                                
        split_pattern = f"({pattern_regex})[^#]*?(?=(?:{'|'.join(escaped_patterns)})|$)"
        
                
        matches = re.finditer(split_pattern, text, re.DOTALL)
        
        for match in matches:
            link_text = match.group(0).strip()
            if link_text:
                           
                link_info = self.recognize(link_text)
                if link_info:
                    results.append(link_info)
        
                            
        if not results:
                   
            parts = text.split()
            for part in parts:
                part = part.strip()
                if part:
                    link_info = self.recognize(part)
                    if link_info:
                        results.append(link_info)
        
        return results

    
    def recognize_with_positions(self, text: str) -> List[Dict[str, Any]]:
        """
        识别文本中的链接并保留位置信息，用于保留原始格式
        
        Args:
            text: 用户输入的文本
            
        Returns:
            识别结果列表，每个元素包含：
            {
                "type": "链接类型",
                "content": "完整链接内容",
                "start": 起始位置,
                "end": 结束位置,
                "original": "原始文本片段（包含前后分隔符）"
            }
        """
        results = []
        
                       
        all_patterns = []
        for link_type, pattern_config in self.config.items():
            patterns = pattern_config.get("patterns", [])
            if not patterns:
                pattern = pattern_config.get("pattern", "")
                if pattern:
                    patterns = [pattern]
            all_patterns.extend(patterns)
        
        if not all_patterns:
            return results
        
                      
        escaped_patterns = [re.escape(p) for p in all_patterns]
        pattern_regex = '|'.join(escaped_patterns)
        
                 
        separators = set([' ', '\t', '，', ',', '。', '；', ';', '\n', '\r'])
        
                                     
                             
        link_pattern = f"({pattern_regex})[^\\s,，。；\\n#]*"
        
                   
        for match in re.finditer(link_pattern, text):
            link_text = match.group(0)
            start_pos = match.start()
            end_pos = match.end()
            link_info = self.recognize(link_text)
            if link_info:
                prev_start = start_pos
                if start_pos > 0:
                    prev_text = text[:start_pos]
                    for i in range(len(prev_text) - 1, -1, -1):
                        if prev_text[i] not in separators:
                            prev_start = start_pos - (len(prev_text) - i - 1)
                            break
                
                next_end = end_pos
                if end_pos < len(text):
                    if text[end_pos] in separators:
                        next_end = end_pos + 1
                    else:
                        remaining_text = text[end_pos:]
                        for pattern in all_patterns:
                            if remaining_text.startswith(pattern):
                                break
                        else:
                            for i, char in enumerate(remaining_text):
                                if char in separators:
                                    next_end = end_pos + i + 1
                                    break
                                elif any(remaining_text[i:].startswith(p) for p in all_patterns):
                                    break
                
                original = text[prev_start:next_end]
                
                results.append({
                    "type": link_info["type"],
                    "content": link_text,
                    "start": start_pos,
                    "end": end_pos,
                    "original": original,
                    "matched_pattern": link_info.get("matched_pattern", "")
                })

        results.sort(key=lambda x: x["start"])
        
        return results


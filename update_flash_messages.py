# update_flash_messages.py
import re

def update_flash_messages():
    try:
        with open('app.py', 'r', encoding='utf-8') as file:
            content = file.read()
        
        # Pattern to find flash statements
        pattern = r'flash\("([^"]+)"\)'
        
        def replace_flash(match):
            message = match.group(1)
            
            # Determine category based on message content
            msg_lower = message.lower()
            
            if any(word in msg_lower for word in ['success', 'successfully', 'added', 'updated', 'deleted', 'placed', 'cancelled', 'completed', 'welcome', 'marked', 'approved', 'paid']):
                return f'flash("{message}", "success")'
            elif any(word in msg_lower for word in ['error', 'invalid', 'incorrect', 'failed', 'cannot', 'required', 'not found', 'wrong', 'problem', 'access denied']):
                return f'flash("{message}", "error")'
            elif any(word in msg_lower for word in ['warning', 'empty', 'please select', 'try again', 'not in cart', 'please', 'check']):
                return f'flash("{message}", "warning")'
            else:
                return f'flash("{message}", "info")'
        
        # Replace all flash statements
        updated_content = re.sub(pattern, replace_flash, content)
        
        # Write back to file
        with open('app.py', 'w', encoding='utf-8') as file:
            file.write(updated_content)
        
        print("✅ Flash messages updated successfully!")
        print("📝 Please check app.py to verify the changes.")
        
    except FileNotFoundError:
        print("❌ Error: app.py not found in current directory")
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    update_flash_messages()
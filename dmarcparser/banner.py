MAGENTA = "\033[95m"
RESET = "\033[0m"
RESET  = "\033[0m"
CYAN   = "\033[36m"
MAGENTA = "\033[35m"
GREEN  = "\033[32m"
YELLOW = "\033[33m"
RED    = "\033[31m"
BLUE   = "\033[34m"
WHITE  = "\033[37m"
ORANGE = "\033[38;5;208m"

BLACK_TEXT   = "\033[30m"       # standard black foreground
ORANGE_BG    = "\033[48;5;208m"

ascii_banner = r'''
                                                               ,ggggggggggg,                                                    
         8I                                                   dP"""88""""""Y8,                                                  
         8I                                                   Yb,  88      `8b                                                  
         8I                                                    `"  88      ,8P                                                  
         8I                                                        88aaaad8P"                                                   
   ,gggg,8I   ,ggg,,ggg,,ggg,     ,gggg,gg   ,gggggg,    ,gggg,    88"""""    ,gggg,gg   ,gggggg,    ,g,      ,ggg,    ,gggggg, 
  dP"  "Y8I  ,8" "8P" "8P" "8,   dP"  "Y8I   dP""""8I   dP"  "Yb   88        dP"  "Y8I   dP""""8I   ,8'8,    i8" "8i   dP""""8I 
 i8'    ,8I  I8   8I   8I   8I  i8'    ,8I  ,8'    8I  i8'         88       i8'    ,8I  ,8'    8I  ,8'  Yb   I8, ,8I  ,8'    8I 
,d8,   ,d8b,,dP   8I   8I   Yb,,d8,   ,d8b,,dP     Y8,,d8,_    _   88      ,d8,   ,d8b,,dP     Y8,,8'_   8)  `YbadP' ,dP     Y8,
P"Y8888P"`Y88P'   8I   8I   `Y8P"Y8888P"`Y88P      `Y8P""Y8888PP   88      P"Y8888P"`Y88P      `Y8P' "YY8P8P888P"Y8888P      `Y8
'''


def dmarc_banner():
    print(f"{ORANGE}{ascii_banner}{RESET}")

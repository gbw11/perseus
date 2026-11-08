/* SHA helper의 byte/인자 경계를 Python hashlib와 대조하는 target-only probe. */
#include <stdio.h>
#include <string.h>
#include <xenova/sha256.h>

static uint8_t input[32770],original[32770];
static int invalid_arguments(void)
{
 uint8_t out[32],saved[32];memset(out,0xa5,sizeof(out));memcpy(saved,out,sizeof(out));
 for(size_t i=0;i<sizeof(input);i++)input[i]=(uint8_t)i;
 memcpy(original,input,sizeof(input));
 if(xdr_sha256(NULL,1,out)||memcmp(out,saved,sizeof(out)))return 10;
 if(xdr_sha256(input,1,NULL)||memcmp(input,original,sizeof(input)))return 11;
 if(xdr_sha256(NULL,0,NULL))return 12;
 if(xdr_sha256(input,64,input+1)||memcmp(input,original,sizeof(input)))return 13;
 if(xdr_sha256(input+1,64,input)||memcmp(input,original,sizeof(input)))return 14;
 if((uintmax_t)SIZE_MAX>UINT64_MAX/8u){
  if(xdr_sha256(input,SIZE_MAX,out)||memcmp(out,saved,sizeof(out)))return 15;
 }
 if(!xdr_sha256(NULL,0,out))return 16;
 if(!xdr_sha256(input,0,out))return 17;
 /* out adjacent to, but outside, the input is legal; only the input span must
  * remain unchanged. Both ends and non-word-aligned input are exercised. */
 if(!xdr_sha256(input+32,64,input)||memcmp(input+32,original+32,64))return 18;
 memcpy(input,original,sizeof(input));
 if(!xdr_sha256(input+1,63,input+64)||memcmp(input+1,original+1,63))return 19;
 puts("invalid-arguments OK");return 0;
}
int main(int argc,char **argv)
{
 if(argc==2&&!strcmp(argv[1],"--invalid"))return invalid_arguments();
 if(argc!=2)return 2;
 FILE *stream=fopen(argv[1],"rb");if(!stream)return 3;
 memset(input,0xa5,sizeof(input));
 size_t length=fread(input+1,1,sizeof(input)-2,stream);
 bool bad=ferror(stream)!=0||fgetc(stream)!=EOF;
 if(fclose(stream)||bad)return 4;
 memcpy(original,input,sizeof(input));
 uint8_t digest[32];
 if(!xdr_sha256(input+1,length,digest)||memcmp(input,original,sizeof(input)))return 5;
 for(size_t i=0;i<sizeof(digest);i++)if(printf("%02x",(unsigned)digest[i])<0)return 6;
 if(putchar('\n')==EOF)return 7;
 return 0;
}
